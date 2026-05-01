"""
Парсер 2ГИС для сбора информации о компаниях.
Использует Playwright + asyncio с поддержкой прокси и stealth режима.
Совместим с: pandas 3.0.2, playwright 1.59.0, playwright-stealth 2.0.3, Python 3.13

Особенности:
- Контекстные менеджеры для автоматической очистки ресурсов
- Обработка системных сигналов (SIGINT, SIGTERM)
- Глобальный патч логов asyncio для игнорирования ошибок при завершении
- Флаг _closing для корректного завершения в серверной среде
"""

import asyncio
import random
import math
import csv
import logging
import os
import re
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, urlunparse

import pandas as pd
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from config_2gis import (
    PROXY_CONFIG,
    BROWSER_CONFIG,
    TIMEOUTS,
    DELAYS,
    SELECTORS,
    PARSING_CONFIG,
    OUTPUT_CONFIG,
    FIELDS,
)
from resume_manager import ResumeManager
from proxy_manager import ProxyManager

# ============================================================================
# ГЛОБАЛЬНЫЙ ПАТЧ ЛОГОВ ASYNCIO
# ============================================================================
# Игнорируем ошибки asyncio при завершении работы парсера
def _ignore_asyncio_errors(loop, context):
    """Обработчик ошибок asyncio, игнорирующий ошибки при завершении."""
    exception = context.get('exception')
    
    # Игнорируем типичные ошибки при завершении
    if isinstance(exception, (RuntimeError, BrokenPipeError, ConnectionResetError)):
        error_msg = str(exception).lower()
        if any(msg in error_msg for msg in [
            'event loop is closed',
            'i/o operation on closed pipe',
            'connection lost',
            'cannot schedule new futures',
            'cannot write to closed file',
        ]):
            # Логируем только на уровне debug, не выводим в stderr
            return
    
    # Для остальных ошибок используем стандартный обработчик
    loop.default_exception_handler(context)

# Устанавливаем обработчик ошибок
try:
    asyncio.get_event_loop().set_exception_handler(_ignore_asyncio_errors)
except RuntimeError:
    pass  # Event loop может быть закрыт


class Logger:
    """Логгер для парсера."""

    def __init__(self, log_file: str):
        """Инициализация логгера."""
        self.logger = logging.getLogger("2GIS_Parser")
        self.logger.setLevel(logging.DEBUG)

        # Файловый обработчик
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)

        # Консольный обработчик
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)

        # Формат логов
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)

        self.logger.addHandler(fh)
        self.logger.addHandler(ch)

    def info(self, msg: str):
        """Логирование информации."""
        self.logger.info(msg)

    def error(self, msg: str):
        """Логирование ошибки."""
        self.logger.error(msg)

    def warning(self, msg: str):
        """Логирование предупреждения."""
        self.logger.warning(msg)

    def debug(self, msg: str):
        """Логирование отладки."""
        self.logger.debug(msg)


def clean_text(text: str) -> str:
    """
    Очистка текста: удаляет лишние пробелы, URL-трекеры и мусор.
    
    Args:
        text: Исходный текст
        
    Returns:
        Очищенный текст
    """
    if not text or not isinstance(text, str):
        return "N/A"
    
    text = str(text).strip()
    if not text:
        return "N/A"

    # 1) Оставляем только часть до первого URL.
    # Это защищает от длинных трекеров после названия.
    text = re.split(r"https?://", text, maxsplit=1)[0].strip()

    # 2) Удаляем percent-encoded последовательности (%3D, %2F и т.п.).
    text = re.sub(r"%[0-9A-Fa-f]{2}", "", text)

    # 3) Удаляем query-параметры:
    # - если строка похожа на URL-подобный хвост с ключ=значение (&key=...)
    # - либо если после "?" почти нет пробелов (типичный трекер)
    if "?" in text:
        before_q, after_q = text.split("?", 1)
        query_like = "=" in after_q or "&" in after_q
        mostly_compact_tail = len(after_q) > 0 and (after_q.count(" ") / max(len(after_q), 1)) < 0.08
        if query_like or mostly_compact_tail:
            text = before_q.strip()

    # 4) Удаляем служебные хвосты после URL-энкодинга, если они остались.
    text = re.sub(r"(?:stat|utm_[a-z]+|clid|from|frompanel|ads|erid)\s*[:=_-].*$", "", text, flags=re.IGNORECASE)

    # 5) Схлопываем пробелы и обрезаем слишком длинные строки.
    text = " ".join(text.split())
    if len(text) > 200:
        text = text[:200].rstrip(" ,.;:-")

    # 6) Базовые проверки качества.
    if not text or text.lower().startswith("http"):
        return "N/A"

    return text


def is_valid_text(text: str, max_len: int = 500) -> bool:
    """Проверяет, что текст не является техническим мусором."""
    if text is None:
        return False

    text = str(text).strip()
    if not text:
        return False

    if len(text) > max_len:
        return False

    lower_text = text.lower()
    forbidden_tokens = (
        "__customcfg",
        "var __customcfg",
        "json.parse",
        "sessionid",
        "var __errors",
        "function onwindowerror",
        "__failedresources",
        "window.addevent",
        "window.onerror",
    )
    if any(token in lower_text for token in forbidden_tokens):
        return False

    if lower_text.startswith("var ") or lower_text.startswith("function "):
        return False

    # Очень длинные строки без кириллицы обычно являются тех. мусором.
    if len(text) > 200 and not re.search(r"[а-яА-ЯёЁ]", text):
        return False

    return True


class GisParser:
    """Парсер 2ГИС."""

    def __init__(self, category_url: str, enable_resume: bool = False):
        """
        Инициализация парсера.
        
        Args:
            category_url: URL категории (например, автосервисы в Казани)
            enable_resume: Включить функцию продолжения с места остановки
        """
        self.category_url = category_url
        self.output_dir = Path(OUTPUT_CONFIG["output_dir"])
        
        # Создаем директорию если её нет
        os.makedirs(self.output_dir, exist_ok=True)

        # Инициализация логгера
        log_file = self.output_dir / OUTPUT_CONFIG["log_filename"]
        self.logger = Logger(str(log_file))

        # Менеджер прокси с умной ротацией
        proxy_list = PROXY_CONFIG.get("proxies", []) if PROXY_CONFIG.get("enabled", False) else []
        self.proxy_manager = ProxyManager(proxy_list)
        
        # Счётчик для периодического вывода статистики прокси
        self.companies_since_last_stats = 0
        
        # Менеджер прогресса (resume)
        self.enable_resume = enable_resume
        self.resume_manager: Optional[ResumeManager] = None
        if enable_resume:
            progress_file = self.output_dir / "progress.json"
            self.resume_manager = ResumeManager(str(progress_file), self.logger.logger)
            self.logger.info("✓ Режим Resume включен: парсинг продолжится с места остановки")

        # Данные
        self.companies: List[Dict[str, Any]] = []
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.playwright = None
        
        # Флаг завершения работы (для корректного завершения в серверной среде)
        self._closing = False

        self.logger.info("Парсер инициализирован")
        self.logger.info(f"URL категории: {category_url}")

    async def init_browser(self):
        """Инициализация браузера с Playwright Stealth и контекстными менеджерами."""
        try:
            # Используем контекстный менеджер для автоматической очистки
            self.playwright = await async_playwright().start()

            # Получаем прокси через умный менеджер
            proxy = self.proxy_manager.get_proxy()
            if proxy:
                self.logger.info(f"Используется прокси: {proxy['server']}")

            # Запускаем браузер с флагами для Linux-серверов
            # --no-sandbox: избегаем конфликтов с песочницей на серверах
            # --disable-dev-shm-usage: избегаем проблем с памятью /dev/shm
            launch_args = list(BROWSER_CONFIG["args"])
            if "--no-sandbox" not in launch_args:
                launch_args.append("--no-sandbox")
            if "--disable-dev-shm-usage" not in launch_args:
                launch_args.append("--disable-dev-shm-usage")

            self.browser = await self.playwright.chromium.launch(
                headless=BROWSER_CONFIG["headless"],
                args=launch_args,
                proxy=proxy,
            )

            # Создаем контекст с контекстным менеджером
            self.context = await self.browser.new_context(
                viewport=BROWSER_CONFIG["viewport"],
                user_agent=BROWSER_CONFIG["user_agent"],
            )

            # Применяем stealth режим через JavaScript и встроенные методы
            try:
                # Добавляем JavaScript для скрытия признаков автоматизации
                await self.context.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['ru-RU', 'ru', 'en-US', 'en']
                    });
                    window.chrome = {
                        runtime: {}
                    };
                    """
                )
                self.logger.info("Встроенные методы обхода автоматизации активированы")
            except Exception as e:
                self.logger.debug(f"Ошибка при добавлении init script: {e}")

            headless_mode = "headless" if BROWSER_CONFIG["headless"] else "видимый"
            self.logger.info(f"Браузер инициализирован в {headless_mode} режиме")
            self.logger.info(f"Флаги запуска: {', '.join(launch_args)}")

        except Exception as e:
            self.logger.error(f"Ошибка при инициализации браузера: {e}")
            self._closing = True
            raise

    async def random_delay(self, min_delay: float, max_delay: float):
        """Случайная задержка для имитации человека."""
        delay = random.uniform(min_delay, max_delay)
        await asyncio.sleep(delay)

    async def is_captcha_page(self, page: Page) -> bool:
        """Проверяет, есть ли признаки капчи на странице."""
        try:
            if await page.query_selector("iframe[src*='recaptcha'], div.g-recaptcha, [class*='captcha']"):
                return True

            body_text = (await page.inner_text("body")).lower()
            return "captcha" in body_text or "g-recaptcha" in body_text
        except Exception:
            return False

    async def goto_with_retry_on_http_error(self, page: Page, url: str, wait_until: str = "commit", timeout: int = 60000):
        """
        Переходит по URL с обработкой 429/403:
        при таких кодах ждет 60 секунд и повторяет один раз.
        """
        retries = 2
        for attempt in range(1, retries + 1):
            response = await page.goto(url, wait_until=wait_until, timeout=timeout)
            status = response.status if response else None

            if status in (429, 403):
                self.logger.warning(f"HTTP {status} для {url} (попытка {attempt}/{retries})")
                if attempt < retries:
                    self.logger.warning("Пауза 60 сек перед повторным запросом...")
                    await asyncio.sleep(60)
                    continue
                raise Exception(f"HTTP {status} после повторной попытки: {url}")

            return response

        return None

    def build_page_url(self, base_url: str, page_num: int) -> str:
        """Строит URL страницы поиска в формате /page/{N}."""
        clean_base = base_url.rstrip("/")
        return f"{clean_base}/page/{page_num}"

    def get_base_search_url(self, category_url: str) -> str:
        """
        Возвращает базовый URL поиска без пагинации:
        оставляет часть до `.../search/<query>`.
        """
        parsed = urlparse(category_url)
        path = parsed.path.rstrip("/")
        match = re.match(r"^(.*?/search/[^/]+)", path)
        base_path = match.group(1) if match else path
        return urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", ""))

    async def detect_total_pages(self, page: Page) -> tuple[int, bool]:
        """
        Определяет общее число страниц и режим пагинации.
        
        Returns:
            tuple[int, bool]: (количество_страниц, использовать_адаптивную_пагинацию)
            - Если удалось определить число компаний: (расчётное_число_страниц, False)
            - Если не удалось: (0, True) - адаптивный режим без ограничений
        """
        # Расширенный список селекторов для поиска общего количества
        summary_selectors = [
            'div[class*="search-results-summary"]',      # Основной селектор
            'h1[class*="title"]',                        # Заголовок с количеством
            'span[class*="count"]',                      # Счётчик
            '[data-qa="summary"]',                       # data-qa атрибут
            ".found-counter",                            # Класс счётчика
            'div[class*="summary"]',                     # Общий summary
            'div[class*="results-count"]',               # Счётчик результатов
            'span[class*="total"]',                      # Общее количество
            'h1',                                        # Любой h1 (может содержать количество)
        ]

        total_companies = None
        found_selector = None
        
        for selector in summary_selectors:
            try:
                element = await page.query_selector(selector)
                if not element:
                    continue
                    
                text = clean_text(await element.text_content() or "")
                if text == "N/A":
                    continue
                
                self.logger.debug(f"Проверка селектора {selector}: {text}")
                
                # Удаляем пробелы и ищем все числа
                text_no_spaces = text.replace(" ", "").replace("\xa0", "").replace("\u00a0", "")
                numbers = re.findall(r"\d+", text_no_spaces)
                
                if numbers:
                    # Берем наибольшее число в строке summary
                    candidate = max(int(n) for n in numbers)
                    
                    # Проверяем, что число разумное (больше 0 и меньше 1 миллиона)
                    if 0 < candidate < 1000000:
                        total_companies = candidate
                        found_selector = selector
                        self.logger.info(
                            f"✓ Найдено общее число компаний: {total_companies:,} (selector={selector})"
                        )
                        break
                        
            except Exception as e:
                self.logger.debug(f"Ошибка определения summary по селектору {selector}: {e}")

        # Если удалось определить количество компаний
        if total_companies:
            # Рассчитываем количество страниц (12 карточек на странице)
            total_pages = max(1, math.ceil(total_companies / 12))
            self.logger.info(f"Расчётное количество страниц: {total_pages} (по 12 карточек на странице)")
            
            # Проверяем max_pages из конфига
            max_allowed = PARSING_CONFIG.get("max_pages", 0)
            if max_allowed > 0 and total_pages > max_allowed:
                self.logger.warning(
                    f"Расчётное количество страниц ({total_pages}) превышает max_pages ({max_allowed}). "
                    f"Будет обработано максимум {max_allowed} страниц."
                )
                total_pages = max_allowed
            
            return total_pages, False  # Не нужна адаптивная пагинация
        
        # Если не удалось определить - переключаемся на адаптивный режим
        self.logger.warning(
            "⚠️  Не удалось определить общее число компаний из summary"
        )
        self.logger.info(
            "Перехожу в адаптивный режим (по кнопке «Далее»)"
        )
        
        # Возвращаем 0 (без ограничений) и флаг адаптивной пагинации
        return 0, True  # Использовать адаптивную пагинацию без ограничений

    def _get_next_page_url(self, base_url: str, page_number: int) -> str:
        """
        Формирует URL следующей страницы для прямой пагинации.
        
        Args:
            base_url: Базовый URL категории
            page_number: Номер страницы (начиная с 1)
            
        Returns:
            URL страницы в формате .../page/{page_number}
        """
        # Удаляем существующий /page/N из URL если есть
        clean_url = re.sub(r'/page/\d+/?$', '', base_url.rstrip('/'))
        
        # Для первой страницы возвращаем базовый URL без /page/1
        if page_number == 1:
            return clean_url
        
        # Для остальных страниц добавляем /page/N
        return f"{clean_url}/page/{page_number}"

    async def _check_page_has_results(self, page: Page) -> bool:
        """
        Проверяет, есть ли на странице карточки компаний.
        
        Returns:
            True если найдены карточки, False если страница пустая
        """
        try:
            # Ждём появления карточек (максимум 10 секунд)
            await page.wait_for_selector(
                SELECTORS["company_card"],
                timeout=10000,
                state="visible"
            )
            
            # Проверяем количество карточек
            cards = await page.query_selector_all(SELECTORS["company_card"])
            cards_count = len(cards)
            
            if cards_count > 0:
                self.logger.debug(f"✓ На странице найдено {cards_count} карточек")
                return True
            else:
                self.logger.debug("✗ Карточки не найдены на странице")
                return False
                
        except Exception as e:
            self.logger.debug(f"✗ Ошибка при проверке наличия карточек: {e}")
            return False

    def _get_next_page_url(self, base_url: str, page_number: int) -> str:
        """
        Формирует URL следующей страницы для прямой пагинации.
        
        Args:
            base_url: Базовый URL категории
            page_number: Номер страницы (начиная с 1)
            
        Returns:
            URL страницы в формате .../page/{page_number}
        """
        # Удаляем существующий /page/N из URL если есть
        clean_url = re.sub(r'/page/\d+/?$', '', base_url.rstrip('/'))
        
        # Для первой страницы возвращаем базовый URL без /page/1
        if page_number == 1:
            return clean_url
        
        # Для остальных страниц добавляем /page/N
        return f"{clean_url}/page/{page_number}"

    async def _check_page_has_results(self, page: Page) -> bool:
        """
        Проверяет, есть ли на странице карточки компаний.
        
        Returns:
            True если найдены карточки, False если страница пустая
        """
        try:
            # Ждём появления карточек (максимум 10 секунд)
            await page.wait_for_selector(
                SELECTORS["company_card"],
                timeout=10000,
                state="visible"
            )
            
            # Проверяем количество карточек
            cards = await page.query_selector_all(SELECTORS["company_card"])
            cards_count = len(cards)
            
            if cards_count > 0:
                self.logger.debug(f"✓ На странице найдено {cards_count} карточек")
                return True
            else:
                self.logger.debug("✗ Карточки не найдены на странице")
                return False
                
        except Exception as e:
            self.logger.debug(f"✗ Ошибка при проверке наличия карточек: {e}")
            return False

    async def scroll_results(self, page: Page, scroll_count: int = 5):
        """Умная прокрутка списка результатов для загрузки всех элементов.
        
        Реализует интеллектуальный скроллинг с:
        - Минимум 5 итераций прокрутки
        - Задержками из конфига (DELAYS)
        - Проверкой загрузки новых элементов
        - Логированием прогресса
        """
        try:
            self.logger.info(f"🔄 Начало умной прокрутки списка результатов ({scroll_count} раз)...")
            
            previous_count = 0
            stable_count = 0
            
            for i in range(scroll_count):
                # Получаем текущее количество карточек
                current_cards = await page.query_selector_all(SELECTORS["company_card"])
                current_count = len(current_cards)
                
                self.logger.debug(f"Итерация {i+1}/{scroll_count}: найдено {current_count} карточек")
                
                # Прокручиваем контейнер результатов
                scroll_result = await page.evaluate(
                    """
                    () => {
                        // Пытаемся найти контейнер результатов
                        const containers = [
                            document.querySelector('div[data-qa="search-results"]'),
                            document.querySelector('div[class*="search-results"]'),
                            document.querySelector('div[class*="results-list"]'),
                            document.querySelector('div[role="list"]'),
                            document.querySelector('div[class*="_results"]'),
                        ];
                        
                        let container = null;
                        for (const c of containers) {
                            if (c && c.scrollHeight > 0) {
                                container = c;
                                break;
                            }
                        }
                        
                        if (container) {
                            // Прокручиваем контейнер вниз
                            const oldScrollTop = container.scrollTop;
                            container.scrollTop = container.scrollHeight;
                            return {
                                scrolled: true,
                                scrollHeight: container.scrollHeight,
                                scrollTop: container.scrollTop,
                                clientHeight: container.clientHeight
                            };
                        } else {
                            // Если контейнер не найден, прокручиваем окно
                            window.scrollBy(0, window.innerHeight);
                            return {
                                scrolled: false,
                                message: "Контейнер не найден, прокручено окно"
                            };
                        }
                    }
                    """
                )
                
                self.logger.debug(f"Результат прокрутки: {scroll_result}")
                
                # Используем задержку из конфига
                delay = random.uniform(DELAYS["min_scroll"], DELAYS["max_scroll"])
                self.logger.debug(f"Задержка перед следующей прокруткой: {delay:.2f} сек")
                await asyncio.sleep(delay)
                
                # Проверяем, загрузились ли новые элементы
                if current_count == previous_count:
                    stable_count += 1
                    self.logger.debug(f"Количество карточек не изменилось ({stable_count} раз подряд)")
                else:
                    stable_count = 0
                    self.logger.debug(f"✓ Загружено новых карточек: {current_count - previous_count}")
                
                previous_count = current_count
                
                # Если карточки не загружаются 2 раза подряд, можно остановиться раньше
                if stable_count >= 2 and i >= 2:
                    self.logger.info(f"✓ Карточки перестали загружаться, остановка прокрутки на итерации {i+1}")
                    break
            
            final_cards = await page.query_selector_all(SELECTORS["company_card"])
            self.logger.info(f"✓ Прокрутка завершена. Всего карточек: {len(final_cards)}")
            
        except Exception as e:
            self.logger.warning(f"⚠️  Ошибка при прокрутке: {e}")

    async def safe_get_text_from_selector_array(self, page: Page, selector_array: list) -> str:
        """Безопасное получение текста из массива селекторов.
        
        Итерирует через массив селекторов, пока не найдет непустое значение.
        Поддерживает как CSS селекторы, так и XPath селекторы.
        """
        if not isinstance(selector_array, list):
            # Если это не массив, используем как обычный селектор
            return await self.safe_get_text(page, selector_array)
        
        for selector in selector_array:
            try:
                # Проверяем, это XPath селектор или CSS
                if selector.startswith("/"):
                    # XPath селектор
                    xpath_selector = selector
                    if "not(self::script)" not in xpath_selector:
                        xpath_selector = (
                            selector[:-1] + "[not(self::script)]"
                            if selector.endswith("]")
                            else f"{selector}[not(self::script)]"
                        )
                    elements = await page.locator(f"xpath={xpath_selector}").all()
                    if elements:
                        tag_name = await elements[0].evaluate("el => (el.tagName || '').toLowerCase()")
                        if tag_name == "script":
                            continue
                        text = await elements[0].text_content()
                        if text:
                            cleaned = clean_text(text)
                            if cleaned != "N/A" and is_valid_text(cleaned):
                                self.logger.debug(f"✓ Найден текст по XPath селектору: {xpath_selector}")
                                return cleaned
                else:
                    # CSS селектор
                    element = await page.query_selector(selector)
                    if element:
                        tag_name = await element.evaluate("el => (el.tagName || '').toLowerCase()")
                        if tag_name == "script":
                            continue
                        text = await element.text_content()
                        if text:
                            cleaned = clean_text(text)
                            if cleaned != "N/A" and is_valid_text(cleaned):
                                self.logger.debug(f"✓ Найден текст по CSS селектору: {selector}")
                                return cleaned
            except Exception as e:
                self.logger.debug(f"Селектор не найден: {selector} ({e})")
                continue
        
        self.logger.debug(f"✗ Ни один селектор из массива не вернул значение")
        return "N/A"

    async def safe_get_text(self, page: Page, selector: str) -> str:
        """Безопасное получение текста элемента с очисткой и retry логикой."""
        try:
            element = await page.query_selector(selector)
            if element:
                tag_name = await element.evaluate("el => (el.tagName || '').toLowerCase()")
                if tag_name == "script":
                    return "N/A"
                text = await element.text_content()
                if text:
                    cleaned = clean_text(text)
                    if cleaned != "N/A" and is_valid_text(cleaned):
                        return cleaned
        except Exception as e:
            self.logger.debug(f"Ошибка при получении текста {selector}: {e}")
        return "N/A"

    async def safe_get_attribute(self, page: Page, selector: str, attr: str) -> str:
        """Безопасное получение атрибута элемента с retry логикой для ошибок контекста."""
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                element = await page.query_selector(selector)
                if element:
                    value = await element.get_attribute(attr)
                    if value:
                        value = value.strip()
                        # Игнорируем очень длинные закодированные URL
                        if len(value) < 500:
                            return clean_text(value)
                return "N/A"
            except Exception as e:
                error_msg = str(e).lower()
                
                # Проверяем, это ошибка потери контекста
                if "execution context was destroyed" in error_msg or "context" in error_msg:
                    retry_count += 1
                    if retry_count < max_retries:
                        self.logger.debug(f"⚠️  Потеря контекста при получении {selector}@{attr}, попытка {retry_count}/{max_retries}")
                        await asyncio.sleep(0.5)  # Небольшая задержка перед повтором
                        continue
                    else:
                        self.logger.warning(f"✗ Не удалось получить {selector}@{attr} после {max_retries} попыток")
                        return "N/A"
                else:
                    self.logger.debug(f"Ошибка при получении атрибута {selector}@{attr}: {e}")
                    return "N/A"
        
        return "N/A"

    async def find_element_in_card(self, card, selector_list: str) -> Optional[Any]:
        """Поиск элемента в карточке по списку селекторов (с fallback).
        
        Пытается найти элемент по основному селектору, затем по альтернативным.
        Полезно для поиска названия компании и других полей.
        """
        selectors = [s.strip() for s in selector_list.split(",")]
        
        for selector in selectors:
            try:
                element = await card.query_selector(selector)
                if element:
                    self.logger.debug(f"✓ Найден элемент по селектору: {selector}")
                    return element
            except Exception as e:
                self.logger.debug(f"Селектор не найден: {selector} ({e})")
                continue
        
        self.logger.debug(f"✗ Ни один селектор не найден из списка: {selector_list}")
        return None

    async def safe_get_text_from_card(self, card, selector_list: str) -> str:
        """Безопасное получение текста из карточки с поиском по альтернативным селекторам."""
        try:
            element = await self.find_element_in_card(card, selector_list)
            if element:
                text = await element.text_content()
                if text:
                    return clean_text(text)
        except Exception as e:
            self.logger.debug(f"Ошибка при получении текста из карточки: {e}")
        return "N/A"

    async def safe_get_attribute_from_card(self, card, selector_list: str, attr: str) -> str:
        """Безопасное получение атрибута из карточки с retry логикой."""
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                element = await self.find_element_in_card(card, selector_list)
                if element:
                    value = await element.get_attribute(attr)
                    if value:
                        value = value.strip()
                        if len(value) < 500:
                            return clean_text(value)
                return "N/A"
            except Exception as e:
                error_msg = str(e).lower()
                
                if "execution context was destroyed" in error_msg or "context" in error_msg:
                    retry_count += 1
                    if retry_count < max_retries:
                        self.logger.debug(f"⚠️  Потеря контекста при получении атрибута из карточки, попытка {retry_count}/{max_retries}")
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        self.logger.warning(f"✗ Не удалось получить атрибут после {max_retries} попыток")
                        return "N/A"
                else:
                    self.logger.debug(f"Ошибка при получении атрибута из карточки: {e}")
                    return "N/A"
        
        return "N/A"

    def _parse_rating(self, rating_text: str) -> str:
        """Парсинг рейтинга из текста. Возвращает float или N/A."""
        if rating_text == "N/A" or not rating_text:
            self.logger.debug("Рейтинг не найден (пустой текст)")
            return "N/A"
        
        try:
            # Ищем число в формате X.X или X,X (от 0 до 5)
            # Используем word boundary для точного совпадения
            match = re.search(r"\b([0-5][\.,]\d+)\b", rating_text)
            if match:
                rating_str = match.group(1).replace(",", ".")
                rating_float = float(rating_str)
                # Проверяем, что рейтинг в допустимом диапазоне
                if 0 <= rating_float <= 5:
                    self.logger.debug(f"Рейтинг найден: {rating_float}")
                    return str(rating_float)
        except (ValueError, AttributeError) as e:
            self.logger.debug(f"Ошибка парсинга рейтинга: {e}")
        
        self.logger.debug(f"Рейтинг не найден в тексте: {rating_text}")
        return "N/A"

    def _parse_reviews_count(self, reviews_text: str) -> str:
        """Парсинг количества отзывов из текста. Возвращает int или N/A."""
        if reviews_text == "N/A" or not reviews_text:
            self.logger.debug("Отзывы не найдены (пустой текст)")
            return "N/A"
        
        try:
            # Ищем число перед словом "отзыв" или "review"
            match = re.search(r"(\d+)\s*(?:отзыв|review)", reviews_text, re.IGNORECASE)
            if match:
                reviews_count = int(match.group(1))
                if reviews_count >= 0:
                    self.logger.debug(f"Отзывы найдены: {reviews_count}")
                    return str(reviews_count)
            
            # Если не найдено, пытаемся найти просто число
            match = re.search(r"(\d+)", reviews_text)
            if match:
                reviews_count = int(match.group(1))
                if reviews_count >= 0:
                    self.logger.debug(f"Отзывы найдены (только число): {reviews_count}")
                    return str(reviews_count)
        except (ValueError, AttributeError) as e:
            self.logger.debug(f"Ошибка парсинга отзывов: {e}")
        
        self.logger.debug(f"Отзывы не найдены в тексте: {reviews_text}")
        return "N/A"

    def _parse_category(self, category_text: str) -> str:
        """Парсинг категории из текста. Возвращает очищенный текст или N/A."""
        if category_text == "N/A" or not category_text:
            return "N/A"
        
        try:
            # Удаляем лишние символы и пробелы
            category = category_text.strip()
            # Если категория слишком длинная, обрезаем
            if len(category) > 100:
                category = category[:100].rstrip(" ,.;:-")
            
            if category and is_valid_text(category):
                return category
        except (AttributeError, TypeError):
            pass
        
        return "N/A"

    async def get_phones(self, page: Page) -> str:
        """Получение всех телефонов компании с поиском в родительских контейнерах."""
        try:
            phones = []
            elements = await page.query_selector_all(SELECTORS["detail_phone"])
            
            for element in elements:
                href = await element.get_attribute("href")
                if href and href.startswith("tel:"):
                    phone = href.replace("tel:", "").strip()
                    if phone and phone not in phones:
                        phones.append(phone)
            
            # Если телефоны не найдены, ищем в родительских контейнерах _card
            if not phones:
                self.logger.debug("Телефоны не найдены по основному селектору, ищем в _card контейнерах")
                card_phones = await page.evaluate(
                    """
                    () => {
                        const phones = [];
                        // Ищем все ссылки tel: в контейнерах с _card в классе
                        const cards = document.querySelectorAll('[class*="_card"]');
                        for (let card of cards) {
                            const links = card.querySelectorAll('a[href^="tel:"]');
                            for (let link of links) {
                                const phone = link.href.replace('tel:', '').trim();
                                if (phone && !phones.includes(phone)) {
                                    phones.push(phone);
                                }
                            }
                        }
                        return phones;
                    }
                    """
                )
                if card_phones:
                    phones = card_phones
            
            return ", ".join(phones) if phones else "N/A"
        except Exception as e:
            self.logger.debug(f"Ошибка при получении телефонов: {e}")
            return "N/A"

    async def get_hours(self, page: Page) -> str:
        """Получение часов работы компании с поддержкой массива селекторов."""
        # Используем новый метод для работы с массивом селекторов
        hours = await self.safe_get_text_from_selector_array(page, SELECTORS["detail_hours"])
        
        if hours != "N/A":
            return hours
        
        # Если не найдено по селекторам, пытаемся найти через JavaScript
        try:
            self.logger.debug("Часы работы не найдены по селекторам, ищем через JavaScript")
            hours_js = await page.evaluate(
                """
                () => {
                    const allText = document.body.innerText;
                    
                    // Ищем паттерны времени работы
                    const patterns = [
                        /Ежедневно[^\\n]*/,
                        /Сегодня[^\\n]*/,
                        /Круглосуточно/,
                        /\\d{1,2}:\\d{2}\\s*-\\s*\\d{1,2}:\\d{2}/,
                        /До\\s+\\d{1,2}:\\d{2}/
                    ];
                    
                    for (let pattern of patterns) {
                        const match = allText.match(pattern);
                        if (match) {
                            return match[0].trim();
                        }
                    }
                    
                    return null;
                }
                """
            )
            if hours_js:
                return hours_js.strip()
        except Exception as e:
            self.logger.debug(f"Ошибка при поиске часов через JavaScript: {e}")
        
        return "N/A"

    async def get_address(self, page: Page) -> str:
        """Получение адреса компании с поддержкой массива селекторов."""
        # Используем новый метод для работы с массивом селекторов
        address = await self.safe_get_text_from_selector_array(page, SELECTORS["company_address"])
        
        if address != "N/A":
            return address
        
        # Если не найдено, ищем в родительских контейнерах _card
        try:
            self.logger.debug("Адрес не найден по селекторам, ищем в _card контейнерах")
            card_address = await page.evaluate(
                """
                () => {
                    // Ищем адрес в контейнерах с _card в классе
                    const cards = document.querySelectorAll('[class*="_card"]');
                    for (let card of cards) {
                        // Ищем элементы с адресом
                        const addressElems = card.querySelectorAll('[class*="address"], [data-qa*="address"]');
                        for (let elem of addressElems) {
                            const text = elem.textContent.trim();
                            if (text && text.length > 3) {
                                return text;
                            }
                        }
                    }
                    return null;
                }
                """
            )
            if card_address:
                return card_address.strip()
        except Exception as e:
            self.logger.debug(f"Ошибка при поиске адреса в _card: {e}")
        
        return "N/A"

    async def extract_company_data(self, page: Page, card_index: int) -> Optional[Dict[str, Any]]:
        """Извлечение данных компании из карточки с улучшенным поиском селекторов."""
        try:
            # Получаем все карточки
            cards = await page.query_selector_all(SELECTORS["company_card"])
            
            if card_index >= len(cards):
                self.logger.debug(f"Индекс карточки {card_index} превышает количество карточек {len(cards)}")
                return None
            
            card = cards[card_index]

            # Получаем название компании с поиском по альтернативным селекторам
            name = await self.safe_get_text_from_card(card, SELECTORS["company_name"])
            if name == "N/A":
                self.logger.debug(f"✗ Не найдено название компании в карточке {card_index}")
                return None

            # Получаем остальные данные из карточки
            address = await self.safe_get_text_from_card(card, SELECTORS["company_address"])
            if address == "N/A":
                self.logger.debug(f"Не найден адрес в карточке {card_index}")
            
            # Извлечение категории с обработкой
            category_raw = await self.safe_get_text_from_card(card, SELECTORS["company_category"])
            category = self._parse_category(category_raw)
            if category == "N/A":
                self.logger.debug(f"Не найдена категория в карточке {card_index}")
            
            # Извлечение рейтинга с парсингом числового значения
            rating_raw = await self.safe_get_text_from_card(card, SELECTORS["company_rating"])
            rating = self._parse_rating(rating_raw)
            if rating == "N/A":
                self.logger.debug(f"Не найден рейтинг в карточке {card_index}")
            
            # Извлечение количества отзывов с парсингом числового значения
            reviews_raw = await self.safe_get_text_from_card(card, SELECTORS["company_reviews"])
            reviews = self._parse_reviews_count(reviews_raw)
            if reviews == "N/A":
                self.logger.debug(f"Не найдены отзывы в карточке {card_index}")

            # Получаем URL компании с retry логикой
            company_url = await self.safe_get_attribute_from_card(card, SELECTORS["company_name"], "href")
            if company_url == "N/A":
                self.logger.debug(f"Не найден URL компании в карточке {card_index}")

            # Детальные данные
            phones = "N/A"
            website = "N/A"
            hours = "N/A"

            if PARSING_CONFIG["open_detail_page"] and company_url != "N/A":
                try:
                    # Открываем детальную страницу
                    detail_page = await self.context.new_page()
                    
                    await detail_page.goto(
                        company_url,
                        wait_until="domcontentloaded",
                        timeout=TIMEOUTS["navigation"],
                    )

                    # Получаем детальные данные
                    phones = await self.get_phones(detail_page)
                    if phones == "N/A":
                        self.logger.debug(f"Не найден селектор телефона")
                    
                    website = await self.safe_get_attribute(
                        detail_page, SELECTORS["detail_website"], "href"
                    )
                    if website == "N/A":
                        self.logger.debug(f"Не найден селектор веб-сайта")
                    
                    hours = await self.get_hours(detail_page)
                    if hours == "N/A":
                        self.logger.debug(f"Не найден селектор часов работы")

                    await detail_page.close()

                    # Задержка перед следующим запросом
                    await self.random_delay(DELAYS["min_page"], DELAYS["max_page"])

                except Exception as e:
                    self.logger.debug(f"Ошибка при получении детальных данных: {e}")

            # Собираем данные
            company_data = {
                "name": name,
                "address": address,
                "category": category,
                "rating": rating,
                "reviews_count": reviews,
                "phones": phones,
                "website": website,
                "hours": hours,
                "url": company_url,
                "parsed_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            return company_data

        except Exception as e:
            self.logger.error(f"Ошибка при извлечении данных компании: {e}")
            return None

    async def parse_page(self, page: Page) -> List[Dict[str, Any]]:
        """Парсинг одной страницы."""
        companies = []

        try:
            # Прокручиваем список результатов для загрузки всех элементов
            await self.scroll_results(page, scroll_count=5)
            
            # Ждем загрузки карточек
            try:
                await page.wait_for_selector(
                    SELECTORS["company_card"],
                    timeout=TIMEOUTS["element_wait"],
                )
            except Exception as timeout_error:
                # Если селектор не найден, делаем скриншот для диагностики
                screenshot_path = self.output_dir / f"error_screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                try:
                    await page.screenshot(path=str(screenshot_path))
                    self.logger.error(f"Селектор '{SELECTORS['company_card']}' не найден. Скриншот сохранен: {screenshot_path}")
                except Exception as screenshot_error:
                    self.logger.error(f"Ошибка при сохранении скриншота: {screenshot_error}")
                
                self.logger.error(f"Ошибка при ожидании селектора: {timeout_error}")
                raise

            # СНАЧАЛА собираем все ссылки на фирмы в список
            firm_links = await page.query_selector_all("a[href*='/firm/']")
            links = []
            
            for link_elem in firm_links:
                try:
                    firm_url = await link_elem.get_attribute("href")
                    if firm_url:
                        # Преобразуем относительный URL в абсолютный если нужно
                        if firm_url.startswith("/"):
                            firm_url = "https://2gis.ru" + firm_url
                        links.append(firm_url)
                except Exception as e:
                    self.logger.debug(f"Ошибка при получении href: {e}")
                    continue
            
            self.logger.info(f"Найдено {len(links)} ссылок на фирмы на странице")
            
            # Фильтруем ссылки через ResumeManager если включен режим resume
            if self.enable_resume and self.resume_manager:
                original_count = len(links)
                links = self.resume_manager.get_remaining_urls(links)
                skipped = original_count - len(links)
                if skipped > 0:
                    self.logger.info(f"Пропущено (уже собрано): {skipped} компаний")

            # ТОЛЬКО ПОСЛЕ того, как список ссылок собран, запускаем цикл.
            # ВАЖНО: парсим карточки фирм в отдельной вкладке, чтобы не терять страницу списка.
            companies_processed_in_batch = 0
            
            for i, firm_url in enumerate(links):
                # Проверяем лимит компаний (0 = без ограничений)
                max_companies = PARSING_CONFIG.get("max_companies", 0)
                if max_companies > 0 and len(self.companies) >= max_companies:
                    self.logger.info(
                        f"Достигнут лимит компаний: {max_companies}"
                    )
                    return companies

                try:
                    self.logger.debug(f"Обработка ссылки {i+1}/{len(links)}: {firm_url}")

                    # Минимальная задержка между запросами
                    await self.random_delay(DELAYS["min_page"], DELAYS["max_page"])

                    detail_page = await self.context.new_page()

                    # Переходим на страницу фирмы с режимом "commit" (самый быстрый)
                    try:
                        await self.goto_with_retry_on_http_error(
                            detail_page,
                            firm_url,
                            wait_until="commit",
                            timeout=60000,
                        )
                    except Exception as goto_error:
                        error_msg = str(goto_error).lower()
                        self.logger.warning(f"Ошибка при переходе на {firm_url}: {goto_error}")
                        
                        # Определяем тип ошибки для статистики прокси
                        if "429" in error_msg or "403" in error_msg:
                            self.proxy_manager.mark_failure("429" if "429" in error_msg else "403")
                            self.proxy_manager.switch_proxy("HTTP блокировка")
                        elif "timeout" in error_msg or "connection" in error_msg:
                            self.proxy_manager.mark_failure("connection")
                        else:
                            self.proxy_manager.mark_failure("unknown")
                        
                        await detail_page.close()
                        continue

                    if await self.is_captcha_page(detail_page):
                        self.logger.error("🔴 Обнаружена капча на странице фирмы")
                        self.proxy_manager.mark_failure("captcha")
                        await detail_page.close()
                        
                        # Пытаемся переключить прокси и продолжить
                        self.proxy_manager.switch_proxy("captcha")
                        self.logger.info("Попытка продолжить с другим прокси...")
                        continue
                    
                    # Ждем появления заголовка h1 (название фирмы)
                    try:
                        await detail_page.wait_for_selector("h1", timeout=TIMEOUTS["element_wait"])
                        self.logger.debug(f"Заголовок h1 найден для {firm_url}")
                    except Exception as h1_error:
                        self.logger.warning(f"Заголовок h1 не найден для {firm_url}: {h1_error}")

                    # Извлекаем данные компании со страницы фирмы
                    company_data = await self.extract_company_data_from_firm_page(detail_page, firm_url)

                    if company_data:
                        companies.append(company_data)
                        self.companies.append(company_data)
                        self.logger.info(f"Собрана компания: {company_data['name']}")
                        
                        # Отмечаем успешный запрос через прокси
                        self.proxy_manager.mark_success()
                        
                        # Отмечаем URL как обработанный в ResumeManager
                        if self.enable_resume and self.resume_manager:
                            self.resume_manager.mark_processed(firm_url)
                            companies_processed_in_batch += 1
                            
                            # Сохраняем прогресс каждые 10 компаний
                            if companies_processed_in_batch % 10 == 0:
                                self.resume_manager.save()
                                self.logger.debug(f"Прогресс сохранён: {companies_processed_in_batch} компаний в текущей сессии")
                        
                        # Периодический вывод статистики прокси (каждые 100 компаний)
                        self.companies_since_last_stats += 1
                        if self.companies_since_last_stats >= 100:
                            self.logger.info(f"📊 {self.proxy_manager.get_short_stats()}")
                            self.companies_since_last_stats = 0
                    else:
                        self.logger.debug(f"Не удалось извлечь данные для {firm_url}")

                    await detail_page.close()

                    # Минимальная задержка перед следующей компанией
                    await self.random_delay(DELAYS["min_page"], DELAYS["max_page"])

                except Exception as e:
                    self.logger.debug(f"Ошибка при обработке ссылки {i+1}: {e}")
                    continue
            
            # Сохраняем прогресс после обработки всех ссылок на странице
            if self.enable_resume and self.resume_manager and companies_processed_in_batch > 0:
                self.resume_manager.save()
                self.logger.info(f"Прогресс сохранён после обработки страницы: +{companies_processed_in_batch} компаний")

        except Exception as e:
            self.logger.error(f"Ошибка при парсинге страницы: {e}")

        return companies

    async def extract_company_data_from_firm_page(self, page: Page, firm_url: str) -> Optional[Dict[str, Any]]:
        """Извлечение данных компании со страницы фирмы без чтения body.innerText."""
        try:
            # Имя компании: сохраняем рабочую логику извлечения из h1.
            name = "N/A"
            h1 = await page.query_selector("h1")
            if h1:
                h1_text = await h1.evaluate(
                    """
                    (el) => {
                        let text = '';
                        for (const node of el.childNodes) {
                            if (node.nodeType === Node.TEXT_NODE) {
                                text += ' ' + node.textContent;
                            } else if (node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'A') {
                                text += ' ' + node.textContent;
                            }
                        }
                        return (text || el.textContent || '').trim();
                    }
                    """
                )
                name = clean_text(h1_text)
            if not is_valid_text(name):
                name = "N/A"
            if name == "N/A":
                self.logger.debug(f"Название компании не найдено для {firm_url}")
                return None

            # Ждем появления хотя бы одного ключевого элемента боковой панели.
            key_selector = (
                f"{SELECTORS['detail_phone']}, "
                f"{SELECTORS['detail_address']}, "
                "[data-qa*='address'], [class*='address']"
            )
            try:
                await page.wait_for_selector(key_selector, timeout=15000)
            except Exception:
                self.logger.debug("Ключевые элементы не появились за 15 сек, fallback-пауза 3 сек")
                await page.wait_for_timeout(3000)

            # Телефоны - через существующий проверенный метод.
            phones = await self.get_phones(page)

            # Адрес: сначала из detail_address, затем fallback по ключевым словам.
            address = await self.safe_get_text_from_selector_array(page, SELECTORS["detail_address"])
            if address == "N/A":
                address = await self.safe_get_text_from_selector_array(page, SELECTORS["company_address"])

            if address == "N/A":
                address_elem = page.locator(
                    "xpath=//*[contains(translate(normalize-space(.), "
                    "'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', "
                    "'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'ул.') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'улица') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'пр.') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'проспект') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'шоссе') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'пер.') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'проезд') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'наб.') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'пл.') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'площадь') "
                    "or contains(translate(normalize-space(.), 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'мкр.')]"
                ).first
                if await address_elem.count() > 0:
                    raw_address = await address_elem.text_content()
                    address = clean_text(raw_address) if raw_address else "N/A"
            if not is_valid_text(address) or len(address.strip()) == 0:
                address = "N/A"

            # Категория.
            category = "N/A"
            category_selectors = [
                "nav[aria-label='Breadcrumb'] span:last-child",  # Хлебные крошки
                "nav[class*='breadcrumb'] span:last-child",
                "[data-qa*='category']",
                "[class*='category']",
                "[data-qa='search-result-category']",
                "[data-qa='card-category']",
                "span[class*='breadcrumb']:last-child",
            ]
            for selector in category_selectors:
                try:
                    category = await self.safe_get_text(page, selector)
                    if category != "N/A" and is_valid_text(category):
                        self.logger.debug(f"Категория найдена через селектор {selector}: {category}")
                        break
                except Exception as e:
                    self.logger.debug(f"Ошибка при поиске категории по селектору {selector}: {e}")
            
            if category == "N/A":
                category = await self.safe_get_text_from_selector_array(page, SELECTORS["detail_category"])
                if category != "N/A":
                    self.logger.debug(f"Категория найдена через detail_category: {category}")
            
            if category == "N/A":
                category = await self.safe_get_text_from_selector_array(page, SELECTORS["company_category"])
                if category != "N/A":
                    self.logger.debug(f"Категория найдена через company_category: {category}")
            
            if category == "N/A":
                self.logger.debug("Категория не найдена ни одним селектором")
            
            if not is_valid_text(category):
                category = "N/A"

            # Рейтинг.
            rating = "N/A"
            rating_selectors = [
                "[itemprop='ratingValue']",                  # Schema.org микроразметка
                "span[class*='_rating']",
                "span[data-qa='rating-value']",
                "span[data-qa*='rating']",
                "div[class*='rating'] span",
                "div[class*='rating']",
                "meta[itemprop='ratingValue']",              # Meta тег с рейтингом
            ]
            for selector in rating_selectors:
                try:
                    # Для meta тега берем атрибут content
                    if selector.startswith("meta"):
                        element = await page.query_selector(selector)
                        if element:
                            rating_text = await element.get_attribute("content")
                            if rating_text:
                                self.logger.debug(f"Рейтинг найден через meta[itemprop]: {rating_text}")
                                rating = self._parse_rating(rating_text)
                                if rating != "N/A":
                                    break
                    else:
                        rating_text = await self.safe_get_text(page, selector)
                        if rating_text != "N/A":
                            self.logger.debug(f"Рейтинг найден через селектор {selector}: {rating_text}")
                            rating = self._parse_rating(rating_text)
                            if rating != "N/A":
                                break
                except Exception as e:
                    self.logger.debug(f"Ошибка при поиске рейтинга по селектору {selector}: {e}")
            
            if rating == "N/A":
                rating_text = await self.safe_get_text_from_selector_array(page, SELECTORS["detail_rating"])
                if rating_text != "N/A":
                    self.logger.debug(f"Рейтинг найден через detail_rating: {rating_text}")
                    rating = self._parse_rating(rating_text)
            
            if rating == "N/A":
                self.logger.debug("Рейтинг не найден ни одним селектором")
            
            if not is_valid_text(str(rating), max_len=50):
                rating = "N/A"

            # Количество отзывов.
            reviews_count = "N/A"
            
            # Сначала пробуем CSS селекторы
            reviews_selectors = [
                "span[class*='reviews']",
                "span[data-qa*='reviews']",
                "div[class*='reviews'] span",
                "a[href*='reviews']",
                "a[href*='otzyvy']",                         # Русский вариант URL
                "[itemprop='reviewCount']",
                "span[class*='_reviewCount']",
                "div[class*='_reviewCount']",
            ]
            
            for selector in reviews_selectors:
                try:
                    reviews_text = await self.safe_get_text(page, selector)
                    if reviews_text != "N/A":
                        self.logger.debug(f"Отзывы найдены через селектор {selector}: {reviews_text}")
                        
                        # Улучшенное регулярное выражение для извлечения числа
                        # Поддерживает форматы: "594 отзыва", "Отзывы594", "594", "Отзывы 594"
                        match = re.search(r"(?:отзыв[а-я]*\s*)?(\d+)", reviews_text, flags=re.IGNORECASE)
                        if not match:
                            # Пробуем английский вариант
                            match = re.search(r"(?:reviews?\s*)?(\d+)", reviews_text, flags=re.IGNORECASE)
                        
                        if match:
                            candidate_reviews = match.group(1)
                            if candidate_reviews.isdigit():
                                reviews_count = candidate_reviews
                                self.logger.debug(f"Отзывы найдены: {reviews_count}")
                                break
                except Exception as e:
                    self.logger.debug(f"Ошибка при поиске отзывов по селектору {selector}: {e}")
            
            # Если не нашли через CSS, пробуем XPath
            if reviews_count == "N/A":
                review_candidates = await page.locator(
                    "xpath=//*[not(self::script)][contains(translate(normalize-space(.), "
                    "'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ', "
                    "'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'), 'отзыв') "
                    "or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'review')]"
                ).all()
                for node in review_candidates[:10]:
                    txt = await node.text_content()
                    cleaned = clean_text(txt) if txt else "N/A"
                    if cleaned == "N/A" or not is_valid_text(cleaned):
                        continue
                    
                    # Улучшенное регулярное выражение
                    match = re.search(r"(?:отзыв[а-я]*\s*)?(\d+)", cleaned, flags=re.IGNORECASE)
                    if not match:
                        match = re.search(r"(?:reviews?\s*)?(\d+)", cleaned, flags=re.IGNORECASE)
                    
                    if match:
                        candidate_reviews = match.group(1)
                        if candidate_reviews.isdigit():
                            reviews_count = candidate_reviews
                            self.logger.debug(f"Отзывы найдены через XPath: {reviews_count}")
                            break
            
            if reviews_count == "N/A":
                reviews_text = await self.safe_get_text_from_selector_array(page, SELECTORS["detail_reviews"])
                if reviews_text != "N/A" and is_valid_text(reviews_text):
                    self.logger.debug(f"Отзывы найдены через detail_reviews: {reviews_text}")
                    
                    # Улучшенное регулярное выражение
                    match = re.search(r"(?:отзыв[а-я]*\s*)?(\d+)", reviews_text, flags=re.IGNORECASE)
                    if not match:
                        match = re.search(r"(?:reviews?\s*)?(\d+)", reviews_text, flags=re.IGNORECASE)
                    
                    if match:
                        candidate_reviews = match.group(1)
                        if candidate_reviews.isdigit():
                            reviews_count = candidate_reviews
                            self.logger.debug(f"Отзывы найдены: {reviews_count}")
            
            if reviews_count == "N/A":
                self.logger.debug("Отзывы не найдены ни одним селектором")
            
            if reviews_count != "N/A" and (not str(reviews_count).isdigit() or not is_valid_text(str(reviews_count), max_len=20)):
                reviews_count = "N/A"

            # Веб-сайт (исключая внутренние/соц. ссылки).
            website = "N/A"
            website_selectors = (
                SELECTORS["detail_website"]
                if isinstance(SELECTORS["detail_website"], list)
                else [s.strip() for s in SELECTORS["detail_website"].split(",")]
            )
            blocked_domains = (
                "2gis.ru", "yandex", "google", "vk.com", "instagram.com",
                "facebook.com", "t.me", "youtube.com", "ok.ru", "twitter.com", "x.com"
            )
            for selector in website_selectors:
                try:
                    links = await page.query_selector_all(selector)
                    for link in links:
                        href = await link.get_attribute("href")
                        if not href:
                            continue
                        href_l = href.lower()
                        if not href_l.startswith("http"):
                            continue
                        if any(domain in href_l for domain in blocked_domains):
                            continue
                        cleaned_href = clean_text(href)
                    if cleaned_href != "N/A" and is_valid_text(cleaned_href):
                            website = cleaned_href
                            break
                    if website != "N/A":
                        break
                except Exception:
                    continue

            # Часы работы.
            hours = await self.safe_get_text_from_selector_array(page, SELECTORS["detail_hours"])
            if hours == "N/A":
                hours = await self.get_hours(page)
            if not is_valid_text(hours, max_len=300):
                hours = "N/A"
            if hours != "N/A":
                hours_l = hours.lower()
                has_time = re.search(r"\d{1,2}:\d{2}", hours) is not None
                has_keyword = any(k in hours_l for k in ("ежедневно", "круглосуточно", "сегодня"))
                if not has_time and not has_keyword:
                    hours = "N/A"
                elif "до " in hours_l:
                    # Валиден только формат "До HH:MM", а не "До -15% ..."
                    if re.search(r"до\s+-?\d+%", hours_l):
                        hours = "N/A"
                    elif re.search(r"до\s+[a-zа-яё]", hours_l):
                        hours = "N/A"
                    elif re.search(r"до\s+\d{1,2}:\d{2}", hours_l) is None and not has_keyword:
                        hours = "N/A"

            # Если адрес и телефон не найдены, все равно возвращаем валидный словарь.
            if address == "N/A" and phones == "N/A":
                self.logger.debug(f"Не найден адрес и телефон для {firm_url}, возвращаю частичные данные")

            company_data = {
                "name": name,
                "address": clean_text(address),
                "category": clean_text(category),
                "rating": clean_text(str(rating)),
                "reviews_count": reviews_count if str(reviews_count).isdigit() else "N/A",
                "phones": clean_text(phones),
                "website": clean_text(website),
                "hours": clean_text(hours),
                "url": firm_url,
                "parsed_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            self.logger.info(f"Данные извлечены для: {name}")
            return company_data

        except Exception as e:
            self.logger.error(f"Ошибка при извлечении данных со страницы фирмы: {e}")
            try:
                fallback_name = clean_text(await self.safe_get_text(page, "h1"))
                if fallback_name == "N/A":
                    return None
                return {
                    "name": fallback_name,
                    "address": "N/A",
                    "category": "N/A",
                    "rating": "N/A",
                    "reviews_count": "N/A",
                    "phones": "N/A",
                    "website": "N/A",
                    "hours": "N/A",
                    "url": firm_url,
                    "parsed_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            except Exception:
                return None

    async def go_to_next_page(self, page: Page) -> bool:
        """Устаревший метод: пагинация теперь через GET-параметр page."""
        self.logger.warning("go_to_next_page не используется: переход выполняется через URL ?page=N")
        return False

    async def run(self):
        """Основной метод запуска парсера с прямой инкрементальной пагинацией."""
        page = None
        try:
            self.logger.info("=" * 70)
            self.logger.info("Запуск парсера 2ГИС")
            self.logger.info("=" * 70)

            # Инициализация браузера
            await self.init_browser()

            # Создаем страницу
            page = await self.context.new_page()

            # Переходим на URL категории с режимом "commit"
            try:
                await self.goto_with_retry_on_http_error(
                    page,
                    self.category_url,
                    wait_until="commit",
                    timeout=60000,
                )
            except Exception as goto_error:
                error_msg = str(goto_error).lower()
                self.logger.warning(f"Ошибка при переходе на категорию: {goto_error}")
                
                # Отмечаем ошибку в статистике прокси
                if "429" in error_msg or "403" in error_msg:
                    self.proxy_manager.mark_failure("429" if "429" in error_msg else "403")
                elif "timeout" in error_msg or "connection" in error_msg:
                    self.proxy_manager.mark_failure("connection")
                else:
                    self.proxy_manager.mark_failure("unknown")
                
                self.logger.error("Не удалось загрузить страницу категории")
                self._closing = True
                raise

            if await self.is_captcha_page(page):
                self.logger.error("🔴 Обнаружена капча на странице категории")
                self.proxy_manager.mark_failure("captcha")
                self.save_data()
                self._closing = True
                return

            # Определяем общее число страниц
            total_pages_detected, use_adaptive = await self.detect_total_pages(page)
            
            # Определяем лимиты
            max_pages_limit = PARSING_CONFIG.get("max_pages", 0)
            max_companies_limit = PARSING_CONFIG.get("max_companies", 0)
            
            # Логирование режима работы
            self.logger.info("📄 Режим прямой инкрементальной пагинации (переход по URL /page/N)")
            
            if total_pages_detected > 0:
                self.logger.info(f"Определено страниц: {total_pages_detected}")
            
            if max_pages_limit > 0:
                self.logger.info(f"Максимум страниц: {max_pages_limit}")
            else:
                self.logger.info("Максимум страниц: без ограничений")
            
            if max_companies_limit > 0:
                self.logger.info(f"Лимит компаний: {max_companies_limit}")
            else:
                self.logger.info("Лимит компаний: без ограничений")

            # Получаем базовый URL для пагинации
            base_search_url = self.get_base_search_url(self.category_url)
            self.logger.info(f"Базовый URL поиска: {base_search_url}")
            self.logger.info("=" * 70)

            # ПРЯМАЯ ИНКРЕМЕНТАЛЬНАЯ ПАГИНАЦИЯ
            page_num = 1
            max_iterations = max_pages_limit if max_pages_limit > 0 else 10000
            
            while page_num <= max_iterations:
                if self._closing:
                    break

                # Формируем URL страницы через новый метод
                paged_url = self._get_next_page_url(base_search_url, page_num)
                
                # Логирование прогресса
                if total_pages_detected > 0 and max_pages_limit > 0:
                    pages_to_show = min(total_pages_detected, max_pages_limit)
                    self.logger.info(f"📄 Прямой переход на страницу {page_num}/{pages_to_show}: {paged_url}")
                elif total_pages_detected > 0:
                    self.logger.info(f"📄 Прямой переход на страницу {page_num}/{total_pages_detected}: {paged_url}")
                else:
                    self.logger.info(f"📄 Прямой переход на страницу {page_num}: {paged_url}")

                # Переходим на страницу
                try:
                    await self.goto_with_retry_on_http_error(
                        page,
                        paged_url,
                        wait_until="commit",
                        timeout=60000,
                    )
                except Exception as goto_page_error:
                    self.logger.warning(f"⚠️  Не удалось открыть страницу {page_num}: {goto_page_error}")
                    self.logger.info("Завершение парсинга: ошибка загрузки страницы")
                    break

                # Ждем загрузки
                try:
                    await page.wait_for_load_state("networkidle", timeout=TIMEOUTS["network_idle"])
                except Exception:
                    # Fallback: минимальная пауза если networkidle не сработал
                    await asyncio.sleep(0.5)

                # Проверка капчи
                if await self.is_captcha_page(page):
                    self.logger.error("🔴 Обнаружена капча в пагинации")
                    self.proxy_manager.mark_failure("captcha")
                    self.save_data()
                    self._closing = True
                    break

                # Проверяем наличие карточек на странице
                has_results = await self._check_page_has_results(page)
                if not has_results:
                    self.logger.info(f"✓ Страница {page_num} пустая или карточки не найдены. Парсинг завершён.")
                    break

                # Парсим текущую страницу
                companies_before = len(self.companies)
                await self.parse_page(page)
                companies_after = len(self.companies)
                companies_added = companies_after - companies_before
                
                self.logger.info(f"✓ Собрано компаний на странице: {companies_added} (всего: {companies_after})")

                # Проверяем лимит компаний
                if max_companies_limit > 0 and len(self.companies) >= max_companies_limit:
                    self.logger.info(f"✓ Достигнут лимит компаний: {max_companies_limit}")
                    break

                # Если на странице не было добавлено ни одной компании - завершаем
                if companies_added == 0:
                    self.logger.warning(f"⚠️  На странице {page_num} не найдено новых компаний")
                    self.logger.info("Завершение парсинга: пустая страница")
                    break

                # Переходим к следующей странице
                page_num += 1
                
                # Проверяем, не превысили ли расчётное количество страниц
                if total_pages_detected > 0 and page_num > total_pages_detected:
                    self.logger.info(f"✓ Обработаны все расчётные страницы ({total_pages_detected})")
                    break

            # Сохраняем данные
            self.save_data()

            self.logger.info("=" * 70)
            self.logger.info(f"✓ Парсинг завершен. Собрано {len(self.companies)} компаний")
            
            # Статистика resume
            if self.enable_resume and self.resume_manager:
                stats = self.resume_manager.get_stats()
                self.logger.info(f"Всего обработано за все сессии: {stats['total_collected']} компаний")
                self.logger.info(f"Файл прогресса: {stats['progress_file']}")
            
            # Финальная статистика прокси
            self.logger.info("")
            self.logger.info(self.proxy_manager.get_stats_report())
            
            self.logger.info("=" * 70)

        except Exception as e:
            if not self._closing:
                self.logger.error(f"Критическая ошибка: {e}")
            # Пытаемся сохранить собранные данные даже при ошибке
            try:
                self.save_data()
            except Exception as save_error:
                self.logger.warning(f"Ошибка при сохранении данных: {save_error}")

        finally:
            # Корректное завершение работы
            await self._cleanup(page)

    async def _cleanup(self, page: Optional[Page] = None):
        """Корректное завершение работы парсера с очисткой ресурсов."""
        self._closing = True
        
        try:
            # Закрываем страницу
            if page:
                try:
                    await page.close()
                except Exception as e:
                    if not self._closing:
                        self.logger.debug(f"Ошибка при закрытии страницы: {e}")

            # Закрываем контекст
            if self.context:
                try:
                    await self.context.close()
                except Exception as e:
                    if not self._closing:
                        self.logger.debug(f"Ошибка при закрытии контекста: {e}")

            # Закрываем браузер
            if self.browser:
                try:
                    await self.browser.close()
                    self.logger.info("Браузер закрыт")
                except Exception as e:
                    if not self._closing:
                        self.logger.debug(f"Ошибка при закрытии браузера: {e}")

            # Закрываем Playwright
            if self.playwright:
                try:
                    await self.playwright.stop()
                    self.logger.info("Playwright остановлен")
                except Exception as e:
                    if not self._closing:
                        self.logger.debug(f"Ошибка при остановке Playwright: {e}")

        except Exception as e:
            if not self._closing:
                self.logger.debug(f"Ошибка при очистке ресурсов: {e}")

    def save_data(self):
        """Сохранение данных в CSV и Excel с обработкой ошибок."""
        if not self.companies:
            self.logger.warning("Нет данных для сохранения")
            return

        try:
            # Создаем директорию если её нет
            os.makedirs(self.output_dir, exist_ok=True)
            self.logger.debug(f"Директория для сохранения: {self.output_dir}")

            # Создаем DataFrame
            df = pd.DataFrame(self.companies)

            # Переупорядочиваем колонки
            df = df[FIELDS]

            # ================================================================
            # СОХРАНЕНИЕ В CSV (ПРИОРИТЕТ - ВСЕГДА ДОЛЖНО РАБОТАТЬ)
            # ================================================================
            csv_saved = False
            try:
                csv_path = self.output_dir / OUTPUT_CONFIG["csv_filename"]
                df.to_csv(csv_path, index=False, encoding="utf-8-sig")
                self.logger.info(f"✓ Данные сохранены в CSV: {csv_path}")
                csv_saved = True
            except Exception as csv_error:
                self.logger.error(f"✗ Ошибка при сохранении CSV: {csv_error}")
                self.logger.error("Данные могут быть потеряны!")
                raise  # Пробрасываем ошибку, так как CSV - критично

            # ================================================================
            # СОХРАНЕНИЕ В EXCEL (ОПЦИОНАЛЬНО - ЕСЛИ ОШИБКА, ПРОДОЛЖАЕМ)
            # ================================================================
            excel_saved = False
            
            # Проверяем наличие openpyxl
            try:
                import openpyxl
            except ImportError:
                self.logger.warning("⚠️  openpyxl не установлен, пропускаем Excel экспорт")
                self.logger.warning("Установите: pip install openpyxl")
                return
            
            # Функция для сохранения Excel с форматированием
            def save_excel_with_formatting(file_path):
                """Сохраняет Excel файл с автоматическим расширением колонок."""
                with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                    df.to_excel(writer, sheet_name="Компании", index=False)
                    
                    # Автоматическое расширение колонок
                    worksheet = writer.sheets["Компании"]
                    for column in worksheet.columns:
                        max_length = 0
                        column_letter = column[0].column_letter
                        
                        for cell in column:
                            try:
                                if len(str(cell.value)) > max_length:
                                    max_length = len(str(cell.value))
                            except:
                                pass
                        
                        # Устанавливаем ширину колонки (с небольшим запасом)
                        adjusted_width = min(max_length + 2, 50)
                        worksheet.column_dimensions[column_letter].width = adjusted_width
            
            # Пытаемся сохранить в основной файл
            excel_filename = OUTPUT_CONFIG["csv_filename"].replace(".csv", ".xlsx")
            excel_path = self.output_dir / excel_filename
            
            try:
                save_excel_with_formatting(excel_path)
                self.logger.info(f"✓ Данные сохранены в Excel: {excel_path}")
                excel_saved = True
                
            except PermissionError as perm_error:
                # Обработка ошибки Permission denied (файл открыт)
                self.logger.warning(f"⚠️  Основной Excel файл занят (PermissionError)")
                self.logger.warning("Файл может быть открыт в Excel или другой программе")
                
                # Пытаемся найти свободное имя файла с суффиксом _1, _2, _3...
                base_name = OUTPUT_CONFIG["csv_filename"].replace(".csv", "")
                suffix = 1
                max_attempts = 10
                
                while suffix <= max_attempts:
                    excel_filename_alt = f"{base_name}_{suffix}.xlsx"
                    excel_path_alt = self.output_dir / excel_filename_alt
                    
                    try:
                        save_excel_with_formatting(excel_path_alt)
                        self.logger.info(f"✓ Данные сохранены в альтернативный Excel файл: {excel_path_alt}")
                        excel_saved = True
                        break
                    except PermissionError:
                        self.logger.debug(f"Файл {excel_filename_alt} также занят, пробую следующий...")
                        suffix += 1
                        continue
                    except Exception as alt_error:
                        self.logger.warning(f"⚠️  Ошибка при сохранении в {excel_filename_alt}: {alt_error}")
                        suffix += 1
                        continue
                
                if not excel_saved:
                    self.logger.warning(f"⚠️  Не удалось сохранить Excel после {max_attempts} попыток")
                    self.logger.warning("Excel экспорт пропущен, но CSV сохранен успешно")
                    
            except Exception as excel_error:
                # Обработка других ошибок Excel
                self.logger.warning(f"⚠️  Ошибка при сохранении Excel: {excel_error}")
                
                # Пытаемся сохранить во временный файл при других ошибках
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                excel_filename_temp = OUTPUT_CONFIG["csv_filename"].replace(
                    ".csv", f"_{timestamp}.xlsx"
                )
                excel_path_temp = self.output_dir / excel_filename_temp
                
                self.logger.info(f"Попытка fallback сохранения во временный файл: {excel_path_temp}")
                
                try:
                    save_excel_with_formatting(excel_path_temp)
                    self.logger.info(f"✓ Данные сохранены во временный Excel файл (fallback): {excel_path_temp}")
                    excel_saved = True
                    
                except Exception as fallback_error:
                    self.logger.warning(f"⚠️  Fallback сохранение также не удалось: {fallback_error}")
                    self.logger.warning("Excel экспорт пропущен, но CSV сохранен успешно")

            # ================================================================
            # ИТОГОВАЯ СТАТИСТИКА
            # ================================================================
            self.logger.info("=" * 70)
            self.logger.info(f"Парсинг завершен. Собрано {len(self.companies)} компаний")
            self.logger.info("=" * 70)
            
            # Статистика по сохранению
            if csv_saved:
                self.logger.info("✓ CSV экспорт: успешно")
            else:
                self.logger.error("✗ CSV экспорт: ошибка")
            
            if excel_saved:
                self.logger.info("✓ Excel экспорт: успешно")
            else:
                self.logger.warning("⚠️  Excel экспорт: пропущен (см. предупреждения выше)")

        except Exception as e:
            self.logger.error(f"Критическая ошибка при сохранении данных: {e}")
            raise
            if excel_saved:
                self.logger.info("✓ Excel экспорт: успешно")
            else:
                self.logger.warning("⚠️  Excel экспорт: пропущен (но CSV сохранен)")
            
            # Статистика по данным
            if "rating" in df.columns:
                ratings = pd.to_numeric(df["rating"], errors="coerce")
                avg_rating = ratings.mean()
                if not pd.isna(avg_rating):
                    self.logger.info(f"Средний рейтинг: {avg_rating:.2f}")
            
            # Статистика по отзывам
            if "reviews_count" in df.columns:
                reviews = pd.to_numeric(df["reviews_count"], errors="coerce")
                total_reviews = reviews.sum()
                if not pd.isna(total_reviews):
                    self.logger.info(f"Всего отзывов: {int(total_reviews)}")
            
            self.logger.info("=" * 70)

        except Exception as e:
            self.logger.error(f"Критическая ошибка при сохранении данных: {e}")
            self.logger.error("Данные не сохранены!")
            raise


async def main(category_url: str):
    """Точка входа с обработкой системных сигналов."""
    parser = GisParser(category_url)
    
    # Получаем текущий event loop
    loop = asyncio.get_event_loop()
    
    # Функция для обработки сигналов
    def signal_handler(signum, frame):
        """Обработчик системных сигналов (SIGINT, SIGTERM)."""
        signal_name = signal.Signals(signum).name
        parser.logger.warning(f"Получен сигнал {signal_name}, завершаю работу...")
        parser._closing = True
        
        # Отменяем все текущие задачи
        for task in asyncio.all_tasks(loop):
            task.cancel()
    
    # Регистрируем обработчики сигналов
    try:
        # SIGINT (Ctrl+C)
        loop.add_signal_handler(signal.SIGINT, signal_handler, signal.SIGINT, None)
        # SIGTERM (kill -TERM)
        loop.add_signal_handler(signal.SIGTERM, signal_handler, signal.SIGTERM, None)
        parser.logger.info("Обработчики сигналов зарегистрированы")
    except NotImplementedError:
        # На Windows signal handlers работают иначе
        parser.logger.debug("Signal handlers не поддерживаются на этой платформе")
    
    try:
        await parser.run()
    except asyncio.CancelledError:
        parser.logger.info("Парсер был отменен по сигналу")
        parser._closing = True
        # Пытаемся сохранить собранные данные
        try:
            parser.save_data()
        except Exception as e:
            parser.logger.warning(f"Ошибка при сохранении данных: {e}")
    except Exception as e:
        parser.logger.error(f"Ошибка в main: {e}")
        raise
    finally:
        # Убеждаемся, что все ресурсы закрыты
        parser._closing = True


if __name__ == "__main__":
    import argparse
    
    # Парсинг аргументов командной строки
    parser_args = argparse.ArgumentParser(
        description="Парсер 2ГИС (устаревший, используйте run_2gis_parser.py)"
    )
    parser_args.add_argument(
        "--url",
        type=str,
        default="https://2gis.ru/kazan/search/автосервис",
        help="URL категории на 2ГИС"
    )
    
    args = parser_args.parse_args()
    
    print("⚠️  ВНИМАНИЕ: Вы используете устаревший файл parser_2gis.py")
    print("⚠️  Для параллельного режима используйте: run_2gis_parser.py")
    print("⚠️  Пример: python run_2gis_parser.py --url '...' --parallel 2")
    print()
    
    asyncio.run(main(args.url))
