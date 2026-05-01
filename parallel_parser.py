"""
Параллельный парсер 2ГИС с полной изоляцией worker'ов.
Каждый worker запускает свой браузер и обрабатывает компании независимо.
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Error as PlaywrightError

from config_2gis import (
    BROWSER_CONFIG,
    TIMEOUTS,
    SELECTORS,
    PARSING_CONFIG,
)
from resume_manager import ResumeManager
from proxy_manager import ProxyManager

logger = logging.getLogger(__name__)


@dataclass
class ParallelConfig:
    """
    Конфигурация для параллельного парсера.
    
    Attributes:
        category_url: URL категории для парсинга
        num_workers: Количество параллельных worker'ов
        enable_resume: Включить функцию продолжения с места остановки
        output_dir: Директория для сохранения результатов
        max_companies: Максимальное количество компаний (0 = без ограничений)
    """
    category_url: str
    num_workers: int = 3
    enable_resume: bool = False
    output_dir: str = "output_2gis"
    max_companies: int = 0


class ParallelParser:
    """
    Параллельный парсер 2ГИС с полной изоляцией worker'ов.
    
    Особенности:
    - Каждый worker создает свой Playwright instance и браузер
    - Полная изоляция ресурсов между worker'ами
    - Отказоустойчивость: падение одного worker'а не влияет на других
    - Потокобезопасный сбор результатов
    """
    
    def __init__(self, config: ParallelConfig, proxy_manager: ProxyManager):
        """
        Инициализация параллельного парсера.
        
        Args:
            config: Конфигурация парсера
            proxy_manager: Менеджер прокси
        """
        self.config = config
        self.proxy_manager = proxy_manager
        self.url_queue: asyncio.Queue = asyncio.Queue()
        self.results: List[Dict[str, Any]] = []
        self.results_lock = asyncio.Lock()
        
        # Менеджер прогресса
        self.resume_manager: Optional[ResumeManager] = None
        if config.enable_resume:
            progress_file = Path(config.output_dir) / "progress.json"
            self.resume_manager = ResumeManager(str(progress_file), logger)
            logger.info("✓ Режим Resume включен")
        
        # Статистика
        self.total_urls_collected = 0
        self.total_companies_parsed = 0
        self.parse_lock = asyncio.Lock()
    
    async def collect_all_urls(self, max_pages: int = 0) -> int:
        """
        Собирает все URL компаний из поисковой выдачи в очередь.
        Запускается один раз перед запуском worker'ов.
        
        Args:
            max_pages: Максимальное количество страниц (0 = без ограничений)
            
        Returns:
            Количество собранных URL
        """
        logger.info("=" * 70)
        logger.info("📋 Этап 1: Сбор URL компаний из поисковой выдачи")
        logger.info("=" * 70)
        
        all_urls = []
        
        # Запускаем временный браузер для сбора URL
        async with async_playwright() as playwright:
            try:
                # Получаем прокси для сбора URL
                proxy = self.proxy_manager.get_proxy()
                if proxy:
                    logger.info(f"Используется прокси для сбора URL: {proxy['server']}")
                
                browser = await playwright.chromium.launch(
                    headless=BROWSER_CONFIG["headless"],
                    args=BROWSER_CONFIG["args"],
                    proxy=proxy,
                )
                
                context = await browser.new_context(
                    viewport=BROWSER_CONFIG["viewport"],
                    user_agent=BROWSER_CONFIG["user_agent"],
                )
                
                page = await context.new_page()
                
                # Переходим на страницу категории
                logger.info(f"Переход на: {self.config.category_url}")
                
                # Retry логика для первой страницы
                for attempt in range(3):
                    try:
                        await page.goto(self.config.category_url, wait_until="domcontentloaded", timeout=60000)
                        break
                    except Exception as e:
                        if attempt < 2:
                            logger.warning(f"Попытка {attempt + 1}/3 не удалась, повтор через 3 сек...")
                            await asyncio.sleep(3)
                        else:
                            raise Exception(f"Не удалось загрузить категорию после 3 попыток: {e}")
                
                await asyncio.sleep(1)
                
                page_num = 1
                max_pages_limit = max_pages if max_pages > 0 else 999
                
                while page_num <= max_pages_limit:
                    logger.info(f"📄 Сбор URL со страницы {page_num}")
                    
                    # Ждем загрузки карточек
                    try:
                        await page.wait_for_selector(SELECTORS["company_card"], timeout=TIMEOUTS["element_wait"])
                    except Exception:
                        logger.warning(f"Карточки не найдены на странице {page_num}")
                        break
                    
                    # Собираем ссылки на фирмы
                    firm_links = await page.query_selector_all("a[href*='/firm/']")
                    page_urls = []
                    
                    for link_elem in firm_links:
                        try:
                            firm_url = await link_elem.get_attribute("href")
                            if firm_url:
                                if firm_url.startswith("/"):
                                    firm_url = "https://2gis.ru" + firm_url
                                
                                # Проверяем через ResumeManager
                                if self.resume_manager and self.resume_manager.is_processed(firm_url):
                                    continue
                                
                                if firm_url not in all_urls:
                                    page_urls.append(firm_url)
                        except Exception as e:
                            logger.debug(f"Ошибка при получении href: {e}")
                            continue
                    
                    all_urls.extend(page_urls)
                    logger.info(f"✓ Собрано {len(page_urls)} URL (всего: {len(all_urls)})")
                    
                    # Проверяем лимит
                    if self.config.max_companies > 0 and len(all_urls) >= self.config.max_companies:
                        logger.info(f"Достигнут лимит URL: {self.config.max_companies}")
                        all_urls = all_urls[:self.config.max_companies]
                        break
                    
                    # Переходим на следующую страницу
                    page_num += 1
                    next_url = self._build_page_url(self.config.category_url, page_num)
                    
                    # Retry логика для пагинации
                    page_loaded = False
                    for attempt in range(3):
                        try:
                            await page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
                            page_loaded = True
                            break
                        except Exception as e:
                            if attempt < 2:
                                logger.warning(f"Попытка загрузки страницы {page_num}, повтор {attempt + 1}/3...")
                                await asyncio.sleep(2)
                            else:
                                logger.warning(f"Не удалось перейти на страницу {page_num} после 3 попыток")
                                break
                    
                    if not page_loaded:
                        break
                    
                    await asyncio.sleep(0.5)
                
                await browser.close()
                
                self.total_urls_collected = len(all_urls)
                logger.info("=" * 70)
                logger.info(f"✓ Сбор URL завершен: {len(all_urls)} компаний")
                logger.info("=" * 70)
                
                # Добавляем URL в очередь
                for url in all_urls:
                    await self.url_queue.put(url)
                
                return len(all_urls)
                
            except Exception as e:
                logger.error(f"Ошибка при сборе URL: {e}")
                return 0
    
    def _build_page_url(self, base_url: str, page_num: int) -> str:
        """
        Формирует URL страницы пагинации.
        
        Args:
            base_url: Базовый URL категории
            page_num: Номер страницы
            
        Returns:
            URL страницы
        """
        import re
        clean_url = re.sub(r'/page/\d+/?', '', base_url.rstrip('/'))
        
        if page_num == 1:
            return clean_url
        
        return f"{clean_url}/page/{page_num}"
    
    async def worker(self, worker_id: int) -> None:
        """
        Изолированный worker со своим браузером.
        Обрабатывает URL из очереди до получения sentinel-значения (None).
        
        Args:
            worker_id: Уникальный ID worker'а
        """
        logger.info(f"[Worker {worker_id}/{self.config.num_workers}] 🚀 Запущен")
        
        processed_count = 0
        
        # Каждый worker создает свой Playwright instance и браузер
        async with async_playwright() as playwright:
            browser = None
            try:
                # Получаем прокси для этого worker'а
                proxy = self.proxy_manager.get_proxy()
                if proxy:
                    logger.info(f"[Worker {worker_id}] Используется прокси: {proxy['server']}")
                
                # Запускаем браузер
                browser = await playwright.chromium.launch(
                    headless=BROWSER_CONFIG["headless"],
                    args=BROWSER_CONFIG["args"],
                    proxy=proxy,
                )
                
                context = await browser.new_context(
                    viewport=BROWSER_CONFIG["viewport"],
                    user_agent=BROWSER_CONFIG["user_agent"],
                )
                
                page = await context.new_page()
                
                logger.info(f"[Worker {worker_id}] Браузер запущен успешно")
                
                # Обрабатываем URL из очереди
                while True:
                    try:
                        # Получаем URL из очереди
                        firm_url = await self.url_queue.get()
                        
                        # Проверяем sentinel-значение
                        if firm_url is None:
                            logger.info(f"[Worker {worker_id}] Получен сигнал завершения")
                            self.url_queue.task_done()
                            break
                        
                        # Обрабатываем компанию
                        try:
                            success = await self._process_company(worker_id, page, firm_url)
                            if success:
                                processed_count += 1
                        except PlaywrightError as e:
                            logger.warning(f"[Worker {worker_id}] Playwright ошибка для {firm_url}: {str(e)[:100]}")
                            self.proxy_manager.mark_failure("connection")
                        except asyncio.TimeoutError:
                            logger.warning(f"[Worker {worker_id}] Timeout для {firm_url}")
                            self.proxy_manager.mark_failure("timeout")
                        except Exception as e:
                            error_msg = str(e)
                            if "Connection closed" not in error_msg and "ERR_ABORTED" not in error_msg:
                                logger.warning(f"[Worker {worker_id}] Ошибка для {firm_url}: {error_msg[:100]}")
                            self.proxy_manager.mark_failure("connection")
                        finally:
                            self.url_queue.task_done()
                        
                        # Небольшая задержка между запросами
                        await asyncio.sleep(0.5)
                        
                    except asyncio.CancelledError:
                        logger.info(f"[Worker {worker_id}] Прерван по запросу")
                        break
                    except Exception as e:
                        logger.error(f"[Worker {worker_id}] Критическая ошибка в цикле: {e}")
                        break
                
                logger.info(f"[Worker {worker_id}] ✓ Завершен (обработано: {processed_count})")
                
            except Exception as e:
                logger.error(f"[Worker {worker_id}] Ошибка инициализации браузера: {e}")
            finally:
                # Закрываем браузер
                if browser:
                    try:
                        await browser.close()
                        logger.debug(f"[Worker {worker_id}] Браузер закрыт")
                    except Exception:
                        pass
    
    async def _process_company(self, worker_id: int, page, firm_url: str) -> bool:
        """
        Обрабатывает одну компанию.
        
        Args:
            worker_id: ID worker'а
            page: Страница Playwright
            firm_url: URL компании
            
        Returns:
            True если успешно, False если ошибка
        """
        logger.debug(f"[Worker {worker_id}] Обработка: {firm_url}")
        
        # Retry логика для загрузки страницы
        max_retries = 3
        page_loaded = False
        
        for retry_count in range(max_retries):
            try:
                await page.goto(firm_url, wait_until="domcontentloaded", timeout=60000)
                page_loaded = True
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if retry_count < max_retries - 1:
                    logger.debug(f"[Worker {worker_id}] Попытка {retry_count + 1}/{max_retries}, повтор...")
                    await asyncio.sleep(2)
                else:
                    logger.warning(f"[Worker {worker_id}] Не удалось загрузить после {max_retries} попыток")
                    return False
        
        if not page_loaded:
            return False
        
        await asyncio.sleep(0.5)
        
        # Проверка капчи
        if await self._is_captcha_page(page):
            logger.warning(f"[Worker {worker_id}] 🔴 Капча обнаружена")
            self.proxy_manager.mark_failure("captcha")
            return False
        
        # Парсим данные компании
        company_data = await self._extract_company_data(page, firm_url)
        
        if company_data:
            # Добавляем в результаты потокобезопасно
            async with self.results_lock:
                self.results.append(company_data)
            
            async with self.parse_lock:
                self.total_companies_parsed += 1
                current_total = self.total_companies_parsed
            
            logger.info(f"[Worker {worker_id}] ✓ Собрана: {company_data['name']} (всего: {current_total})")
            
            # Отмечаем успех в прокси
            self.proxy_manager.mark_success()
            
            # Отмечаем в ResumeManager
            if self.resume_manager:
                self.resume_manager.mark_processed(firm_url)
                
                # Сохраняем прогресс каждые 10 компаний
                if current_total % 10 == 0:
                    self.resume_manager.save()
            
            return True
        
        return False
    
    async def _is_captcha_page(self, page) -> bool:
        """Проверяет наличие капчи на странице."""
        try:
            if await page.query_selector("iframe[src*='recaptcha'], div.g-recaptcha, [class*='captcha']"):
                return True
            
            body_text = (await page.inner_text("body")).lower()
            return "captcha" in body_text or "g-recaptcha" in body_text
        except Exception:
            return False
    
    async def _extract_company_data(self, page, firm_url: str) -> Optional[Dict[str, Any]]:
        """
        Извлекает данные компании со страницы.
        
        Args:
            page: Страница Playwright
            firm_url: URL компании
            
        Returns:
            Словарь с данными компании или None
        """
        try:
            # Ждем загрузки заголовка
            await page.wait_for_selector("h1", timeout=TIMEOUTS["element_wait"])
            
            # Название
            name = "N/A"
            h1 = await page.query_selector("h1")
            if h1:
                name_text = await h1.text_content()
                if name_text:
                    name = name_text.strip()
            
            if name == "N/A":
                return None
            
            # Адрес
            address = await self._safe_get_text(page, SELECTORS["detail_address"])
            
            # Категория
            category = await self._safe_get_text(page, SELECTORS["detail_category"])
            
            # Рейтинг
            rating = await self._safe_get_text(page, SELECTORS["detail_rating"])
            
            # Отзывы
            reviews = await self._safe_get_text(page, SELECTORS["detail_reviews"])
            
            # Телефоны
            phones = await self._get_phones(page)
            
            # Сайт
            website = await self._safe_get_attribute(page, SELECTORS["detail_website"], "href")
            
            # Часы работы
            hours = await self._safe_get_text(page, SELECTORS["detail_hours"])
            
            company_data = {
                "name": name,
                "address": address,
                "category": category,
                "rating": rating,
                "reviews_count": reviews,
                "phones": phones,
                "website": website,
                "hours": hours,
                "url": firm_url,
                "parsed_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            
            return company_data
            
        except Exception as e:
            logger.debug(f"Ошибка извлечения данных: {e}")
            return None
    
    async def _safe_get_text(self, page, selector) -> str:
        """Безопасное получение текста."""
        try:
            if isinstance(selector, list):
                selectors = selector
            elif isinstance(selector, str) and "," in selector:
                selectors = [s.strip() for s in selector.split(",")]
            else:
                selectors = [selector]
            
            for sel in selectors:
                try:
                    element = await page.query_selector(sel)
                    if element:
                        text = await element.text_content()
                        if text and text.strip():
                            return text.strip()
                except Exception:
                    continue
            
            return "N/A"
        except Exception:
            return "N/A"
    
    async def _safe_get_attribute(self, page, selector, attribute: str) -> str:
        """Безопасное получение атрибута."""
        try:
            if isinstance(selector, str) and "," in selector:
                selectors = [s.strip() for s in selector.split(",")]
            else:
                selectors = [selector]
            
            blocked_domains = (
                "2gis.ru", "yandex", "google", "vk.com", "instagram.com",
                "facebook.com", "t.me", "youtube.com", "ok.ru", "twitter.com", "x.com"
            )
            
            for sel in selectors:
                elements = await page.query_selector_all(sel)
                for element in elements:
                    value = await element.get_attribute(attribute)
                    if value and value.startswith("http"):
                        value_lower = value.lower()
                        if not any(domain in value_lower for domain in blocked_domains):
                            return value.strip()
            
            return "N/A"
        except Exception:
            return "N/A"
    
    async def _get_phones(self, page) -> str:
        """Получение телефонов компании."""
        try:
            phones = []
            elements = await page.query_selector_all("a[href^='tel:']")
            
            for element in elements:
                href = await element.get_attribute("href")
                if href and href.startswith("tel:"):
                    phone = href.replace("tel:", "").strip()
                    if phone and phone not in phones:
                        phones.append(phone)
            
            return ", ".join(phones) if phones else "N/A"
        except Exception:
            return "N/A"
    
    async def run(self) -> List[Dict[str, Any]]:
        """
        Запускает параллельный сбор.
        
        Returns:
            Список собранных данных компаний
        """
        logger.info("=" * 70)
        logger.info(f"🚀 ПАРАЛЛЕЛЬНЫЙ ПАРСИНГ ({self.config.num_workers} worker'ов)")
        logger.info("=" * 70)
        
        try:
            # Этап 1: Сбор всех URL
            max_pages = PARSING_CONFIG.get("max_pages", 0)
            url_count = await self.collect_all_urls(max_pages)
            
            if url_count == 0:
                logger.error("Не удалось собрать URL компаний")
                return []
            
            # Добавляем sentinel-значения для каждого worker'а
            for _ in range(self.config.num_workers):
                await self.url_queue.put(None)
            
            logger.info("=" * 70)
            logger.info(f"⚙️  Этап 2: Параллельная обработка {url_count} компаний")
            logger.info("=" * 70)
            
            # Запускаем worker'ов
            workers = [
                asyncio.create_task(self.worker(i + 1))
                for i in range(self.config.num_workers)
            ]
            
            # Ждем завершения всех worker'ов с обработкой исключений
            results = await asyncio.gather(*workers, return_exceptions=True)
            
            # Проверяем результаты
            for i, result in enumerate(results):
                if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                    logger.error(f"Worker {i+1} завершился с ошибкой: {result}")
            
        except KeyboardInterrupt:
            logger.info("\n⚠️  Получен сигнал остановки (Ctrl+C)")
            # Отменяем все worker'ы
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            # Ждем их завершения
            await asyncio.gather(*workers, return_exceptions=True)
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
        finally:
            # Финальное сохранение прогресса
            if self.resume_manager:
                try:
                    self.resume_manager.save()
                except Exception as e:
                    logger.error(f"Ошибка сохранения прогресса: {e}")
        
        logger.info("=" * 70)
        logger.info(f"✓ Параллельный парсинг завершен")
        logger.info(f"Собрано компаний: {len(self.results)}")
        logger.info("=" * 70)
        
        # Статистика прокси
        if len(self.results) > 0:
            logger.info("")
            logger.info(self.proxy_manager.get_stats_report())
        
        return self.results


async def parallel_main(category_url: str, num_workers: int = 3, enable_resume: bool = False, 
                       proxy_list: Optional[List[str]] = None, output_dir: str = "output_2gis",
                       max_companies: int = 0) -> List[Dict[str, Any]]:
    """
    Точка входа для параллельного парсинга.
    
    Args:
        category_url: URL категории для парсинга
        num_workers: Количество параллельных worker'ов
        enable_resume: Включить функцию продолжения
        proxy_list: Список прокси
        output_dir: Директория для результатов
        max_companies: Максимальное количество компаний
        
    Returns:
        Список собранных данных компаний
    """
    config = ParallelConfig(
        category_url=category_url,
        num_workers=num_workers,
        enable_resume=enable_resume,
        output_dir=output_dir,
        max_companies=max_companies,
    )
    
    proxy_manager = ProxyManager(proxy_list)
    parser = ParallelParser(config, proxy_manager)
    
    return await parser.run()
