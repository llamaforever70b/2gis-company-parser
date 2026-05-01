"""
Быстрый сборщик данных из карточек поисковой выдачи 2ГИС.
Не открывает детальные страницы компаний - собирает только базовую информацию.
Производительность: ~2000 компаний/час.
"""

import asyncio
import json
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page, Locator

from config_2gis import SELECTORS, TIMEOUTS

logger = logging.getLogger(__name__)


class QuickCollector:
    """
    Быстрый сборщик. Парсит только карточки из поисковой выдачи.
    Не открывает детальные страницы.
    
    Собирает:
    - Название компании
    - Адрес
    - Категория/рубрика
    - Рейтинг
    - Количество отзывов
    - URL на страницу компании
    - Координаты (если доступны)
    """
    
    def __init__(self, output_dir: str = "output_2gis"):
        """
        Инициализация быстрого сборщика.
        
        Args:
            output_dir: Директория для сохранения результатов
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results: List[Dict[str, Any]] = []
    
    async def collect_from_search_page(self, page: Page) -> List[Dict[str, Any]]:
        """
        Собирает данные со всех карточек на текущей странице поиска.
        
        Args:
            page: Страница Playwright с результатами поиска
            
        Returns:
            Список словарей с данными компаний
        """
        results = []
        
        try:
            # Ждем загрузки карточек
            await page.wait_for_selector(SELECTORS["company_card"], timeout=TIMEOUTS["element_wait"])
            
            # Получаем все карточки
            cards = await page.query_selector_all(SELECTORS["company_card"])
            logger.info(f"Найдено {len(cards)} карточек на странице")
            
            for i, card in enumerate(cards):
                try:
                    company_data = await self._extract_card_data(card, i)
                    if company_data:
                        results.append(company_data)
                        logger.debug(f"✓ Карточка {i+1}: {company_data['name']}")
                except Exception as e:
                    logger.debug(f"Ошибка при обработке карточки {i+1}: {e}")
                    continue
            
            logger.info(f"✓ Собрано {len(results)} компаний с текущей страницы")
            
        except Exception as e:
            logger.error(f"Ошибка при сборе карточек: {e}")
        
        return results
    
    async def _extract_card_data(self, card: Locator, index: int) -> Optional[Dict[str, Any]]:
        """
        Извлекает данные из одной карточки компании.
        
        Args:
            card: Элемент карточки
            index: Индекс карточки
            
        Returns:
            Словарь с данными компании или None
        """
        try:
            # Название компании
            name = await self._get_text_from_card(card, SELECTORS["company_name"])
            if name == "N/A":
                logger.debug(f"Карточка {index+1}: название не найдено")
                return None
            
            # Адрес
            address = await self._get_text_from_card(card, SELECTORS["company_address"])
            
            # Категория
            category = await self._get_text_from_card(card, SELECTORS["company_category"])
            
            # Рейтинг
            rating_text = await self._get_text_from_card(card, SELECTORS["company_rating"])
            rating = self._parse_rating(rating_text)
            
            # Количество отзывов
            reviews_text = await self._get_text_from_card(card, SELECTORS["company_reviews"])
            reviews_count = self._parse_reviews_count(reviews_text)
            
            # URL компании
            url = await self._get_href_from_card(card, SELECTORS["company_name"])
            
            # Координаты (если есть в data-атрибутах)
            coordinates = await self._extract_coordinates(card)
            
            company_data = {
                "name": name,
                "address": address,
                "category": category,
                "rating": rating,
                "reviews_count": reviews_count,
                "url": url,
                "coordinates": coordinates,
                "phones": "N/A",  # Будет заполнено в фазе 2
                "website": "N/A",  # Будет заполнено в фазе 2
                "hours": "N/A",  # Будет заполнено в фазе 2
                "source": "quick_collect",
                "parsed_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            
            return company_data
            
        except Exception as e:
            logger.debug(f"Ошибка извлечения данных из карточки {index+1}: {e}")
            return None
    
    async def _get_text_from_card(self, card: Locator, selector: str) -> str:
        """
        Безопасное получение текста из карточки.
        
        Args:
            card: Элемент карточки
            selector: CSS селектор (может быть списком через запятую)
            
        Returns:
            Текст элемента или "N/A"
        """
        try:
            # Если selector - это список (через запятую)
            if "," in selector:
                selectors = [s.strip() for s in selector.split(",")]
            else:
                selectors = [selector]
            
            for sel in selectors:
                try:
                    element = await card.query_selector(sel)
                    if element:
                        text = await element.text_content()
                        if text and text.strip():
                            return text.strip()
                except Exception:
                    continue
            
            return "N/A"
        except Exception:
            return "N/A"
    
    async def _get_href_from_card(self, card: Locator, selector: str) -> str:
        """
        Безопасное получение href из карточки.
        
        Args:
            card: Элемент карточки
            selector: CSS селектор
            
        Returns:
            URL или "N/A"
        """
        try:
            if "," in selector:
                selectors = [s.strip() for s in selector.split(",")]
            else:
                selectors = [selector]
            
            for sel in selectors:
                try:
                    element = await card.query_selector(sel)
                    if element:
                        href = await element.get_attribute("href")
                        if href:
                            # Преобразуем относительный URL в абсолютный
                            if href.startswith("/"):
                                href = "https://2gis.ru" + href
                            return href
                except Exception:
                    continue
            
            return "N/A"
        except Exception:
            return "N/A"
    
    async def _extract_coordinates(self, card: Locator) -> Optional[Dict[str, float]]:
        """
        Извлекает координаты из data-атрибутов карточки.
        
        Args:
            card: Элемент карточки
            
        Returns:
            Словарь с lat/lon или None
        """
        try:
            # Пытаемся найти координаты в различных data-атрибутах
            for attr in ["data-lat", "data-latitude", "data-coords"]:
                lat = await card.get_attribute(attr)
                if lat:
                    lon = await card.get_attribute(attr.replace("lat", "lon").replace("latitude", "longitude"))
                    if lon:
                        return {"lat": float(lat), "lon": float(lon)}
            
            return None
        except Exception:
            return None
    
    def _parse_rating(self, rating_text: str) -> str:
        """
        Парсит рейтинг из текста.
        
        Args:
            rating_text: Текст с рейтингом
            
        Returns:
            Рейтинг как строка или "N/A"
        """
        if rating_text == "N/A" or not rating_text:
            return "N/A"
        
        try:
            # Ищем число в формате X.X или X,X (от 0 до 5)
            match = re.search(r"\b([0-5][\.,]\d+)\b", rating_text)
            if match:
                rating_str = match.group(1).replace(",", ".")
                rating_float = float(rating_str)
                if 0 <= rating_float <= 5:
                    return str(rating_float)
        except (ValueError, AttributeError):
            pass
        
        return "N/A"
    
    def _parse_reviews_count(self, reviews_text: str) -> str:
        """
        Парсит количество отзывов из текста.
        
        Args:
            reviews_text: Текст с количеством отзывов
            
        Returns:
            Количество отзывов как строка или "N/A"
        """
        if reviews_text == "N/A" or not reviews_text:
            return "N/A"
        
        try:
            # Ищем число
            match = re.search(r"(\d+)", reviews_text)
            if match:
                reviews_count = int(match.group(1))
                if reviews_count >= 0:
                    return str(reviews_count)
        except (ValueError, AttributeError):
            pass
        
        return "N/A"
    
    def add_results(self, results: List[Dict[str, Any]]) -> None:
        """
        Добавляет результаты в общий список.
        
        Args:
            results: Список данных компаний
        """
        self.results.extend(results)
    
    def save_results(self, filename: str = "quick_results.json") -> str:
        """
        Сохраняет результаты в JSON файл.
        
        Args:
            filename: Имя файла для сохранения
            
        Returns:
            Путь к сохраненному файлу
        """
        filepath = self.output_dir / filename
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            
            logger.info(f"✓ Результаты быстрого сбора сохранены: {filepath}")
            logger.info(f"  Всего компаний: {len(self.results)}")
            
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении результатов: {e}")
            raise
    
    def get_results(self) -> List[Dict[str, Any]]:
        """
        Возвращает собранные результаты.
        
        Returns:
            Список данных компаний
        """
        return self.results
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Возвращает статистику по собранным данным.
        
        Returns:
            Словарь со статистикой
        """
        total = len(self.results)
        with_rating = sum(1 for r in self.results if r.get("rating") != "N/A")
        with_reviews = sum(1 for r in self.results if r.get("reviews_count") != "N/A")
        with_coords = sum(1 for r in self.results if r.get("coordinates") is not None)
        
        return {
            "total_companies": total,
            "with_rating": with_rating,
            "with_reviews": with_reviews,
            "with_coordinates": with_coords,
        }
