"""
Детальный сборщик данных для компаний 2ГИС.
Добирает детальную информацию (телефоны, сайты, часы работы) по URL,
собранным в режиме быстрого сбора.
"""

import asyncio
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime

from playwright.async_api import Page, Browser, BrowserContext

from config_2gis import SELECTORS, TIMEOUTS

logger = logging.getLogger(__name__)


class DetailCollector:
    """
    Добирает детальные данные по URL, собранным в фазе Quick.
    
    Добирает:
    - Телефоны
    - Веб-сайт
    - Часы работы
    - Email (если доступен)
    """
    
    def __init__(self, quick_results_file: str, output_dir: str = "output_2gis"):
        """
        Инициализация детального сборщика.
        
        Args:
            quick_results_file: Путь к файлу с результатами быстрого сбора
            output_dir: Директория для сохранения результатов
        """
        self.quick_results_file = Path(quick_results_file)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.base_data: List[Dict[str, Any]] = []
        self.urls_to_process: List[str] = []
        self.enriched_results: List[Dict[str, Any]] = []
        
        self._load_quick_results()
    
    def _load_quick_results(self) -> None:
        """Загружает результаты быстрого сбора из JSON файла."""
        try:
            if not self.quick_results_file.exists():
                raise FileNotFoundError(f"Файл не найден: {self.quick_results_file}")
            
            with open(self.quick_results_file, 'r', encoding='utf-8') as f:
                self.base_data = json.load(f)
            
            # Извлекаем URL для обработки
            self.urls_to_process = [
                item['url'] for item in self.base_data 
                if item.get('url') and item.get('url') != "N/A"
            ]
            
            logger.info(f"✓ Загружено {len(self.base_data)} компаний из {self.quick_results_file}")
            logger.info(f"  URL для обработки: {len(self.urls_to_process)}")
            
        except FileNotFoundError as e:
            logger.error(str(e))
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка чтения JSON: {e}")
            raise
        except Exception as e:
            logger.error(f"Ошибка при загрузке результатов: {e}")
            raise
    
    async def collect_detail(self, page: Page, url: str) -> Dict[str, Any]:
        """
        Собирает детальные данные с одной страницы компании.
        
        Args:
            page: Страница Playwright
            url: URL страницы компании
            
        Returns:
            Словарь с детальными данными
        """
        detail_data = {
            "phones": "N/A",
            "website": "N/A",
            "hours": "N/A",
            "email": "N/A",
        }
        
        try:
            # Переходим на страницу
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUTS["navigation"])
            await asyncio.sleep(0.5)
            
            # Ждем загрузки ключевых элементов
            try:
                await page.wait_for_selector("h1", timeout=TIMEOUTS["element_wait"])
            except Exception:
                logger.debug(f"Заголовок не найден для {url}")
            
            # Телефоны
            detail_data["phones"] = await self._get_phones(page)
            
            # Веб-сайт
            detail_data["website"] = await self._get_website(page)
            
            # Часы работы
            detail_data["hours"] = await self._get_hours(page)
            
            # Email (опционально)
            detail_data["email"] = await self._get_email(page)
            
            logger.debug(f"✓ Детали собраны для {url}")
            
        except Exception as e:
            logger.debug(f"Ошибка при сборе деталей для {url}: {e}")
        
        return detail_data
    
    async def _get_phones(self, page: Page) -> str:
        """
        Получает телефоны компании.
        
        Args:
            page: Страница Playwright
            
        Returns:
            Телефоны через запятую или "N/A"
        """
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
    
    async def _get_website(self, page: Page) -> str:
        """
        Получает веб-сайт компании.
        
        Args:
            page: Страница Playwright
            
        Returns:
            URL сайта или "N/A"
        """
        try:
            selector = SELECTORS.get("detail_website", "a[data-qa='website-link']")
            
            if "," in selector:
                selectors = [s.strip() for s in selector.split(",")]
            else:
                selectors = [selector]
            
            # Исключаем внутренние ссылки
            blocked_domains = (
                "2gis.ru", "yandex", "google", "vk.com", "instagram.com",
                "facebook.com", "t.me", "youtube.com", "ok.ru", "twitter.com", "x.com"
            )
            
            for sel in selectors:
                elements = await page.query_selector_all(sel)
                for element in elements:
                    href = await element.get_attribute("href")
                    if href and href.startswith("http"):
                        href_lower = href.lower()
                        if not any(domain in href_lower for domain in blocked_domains):
                            return href.strip()
            
            return "N/A"
        except Exception:
            return "N/A"
    
    async def _get_hours(self, page: Page) -> str:
        """
        Получает часы работы компании.
        
        Args:
            page: Страница Playwright
            
        Returns:
            Часы работы или "N/A"
        """
        try:
            selector = SELECTORS.get("detail_hours", "div[data-qa='schedule-item']")
            
            # Если selector - это список
            if isinstance(selector, list):
                selectors = selector
            elif "," in selector:
                selectors = [s.strip() for s in selector.split(",")]
            else:
                selectors = [selector]
            
            for sel in selectors:
                element = await page.query_selector(sel)
                if element:
                    text = await element.text_content()
                    if text and text.strip():
                        return text.strip()
            
            return "N/A"
        except Exception:
            return "N/A"
    
    async def _get_email(self, page: Page) -> str:
        """
        Получает email компании (если доступен).
        
        Args:
            page: Страница Playwright
            
        Returns:
            Email или "N/A"
        """
        try:
            # Ищем ссылки mailto:
            elements = await page.query_selector_all("a[href^='mailto:']")
            
            for element in elements:
                href = await element.get_attribute("href")
                if href and href.startswith("mailto:"):
                    email = href.replace("mailto:", "").strip()
                    if email and "@" in email:
                        return email
            
            return "N/A"
        except Exception:
            return "N/A"
    
    def enrich_data(self, url: str, detail_data: Dict[str, Any]) -> None:
        """
        Обогащает базовые данные детальной информацией.
        
        Args:
            url: URL компании
            detail_data: Словарь с детальными данными
        """
        # Находим соответствующую запись в базовых данных
        for item in self.base_data:
            if item.get("url") == url:
                # Обновляем детальные поля
                item["phones"] = detail_data.get("phones", "N/A")
                item["website"] = detail_data.get("website", "N/A")
                item["hours"] = detail_data.get("hours", "N/A")
                item["email"] = detail_data.get("email", "N/A")
                item["source"] = "full_collect"  # Обновляем источник
                item["detail_collected_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                self.enriched_results.append(item)
                break
    
    def save_enriched_results(self, filename: str = "full_results.json") -> str:
        """
        Сохраняет обогащенные результаты в JSON файл.
        
        Args:
            filename: Имя файла для сохранения
            
        Returns:
            Путь к сохраненному файлу
        """
        filepath = self.output_dir / filename
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.enriched_results, f, ensure_ascii=False, indent=2)
            
            logger.info(f"✓ Обогащенные результаты сохранены: {filepath}")
            logger.info(f"  Всего компаний: {len(self.enriched_results)}")
            
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении результатов: {e}")
            raise
    
    def get_urls_to_process(self) -> List[str]:
        """
        Возвращает список URL для обработки.
        
        Returns:
            Список URL
        """
        return self.urls_to_process
    
    def get_enriched_results(self) -> List[Dict[str, Any]]:
        """
        Возвращает обогащенные результаты.
        
        Returns:
            Список данных компаний с детальной информацией
        """
        return self.enriched_results
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Возвращает статистику по обогащенным данным.
        
        Returns:
            Словарь со статистикой
        """
        total = len(self.enriched_results)
        with_phones = sum(1 for r in self.enriched_results if r.get("phones") != "N/A")
        with_website = sum(1 for r in self.enriched_results if r.get("website") != "N/A")
        with_hours = sum(1 for r in self.enriched_results if r.get("hours") != "N/A")
        with_email = sum(1 for r in self.enriched_results if r.get("email") != "N/A")
        
        return {
            "total_enriched": total,
            "with_phones": with_phones,
            "with_website": with_website,
            "with_hours": with_hours,
            "with_email": with_email,
        }
