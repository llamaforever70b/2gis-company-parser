"""
Вспомогательные утилиты для парсера.
"""

import re
import hashlib
from typing import Dict, Any, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


def clean_price(price_text: str) -> int:
    """
    Очищает текст цены и преобразует в число.
    
    Args:
        price_text: Текст с ценой
        
    Returns:
        Цена как целое число
    """
    if not price_text:
        return 0
    
    # Удаляем все нецифровые символы, кроме минуса
    cleaned = re.sub(r"[^\d-]", "", price_text)
    
    try:
        return int(cleaned) if cleaned else 0
    except ValueError:
        return 0


def normalize_url(url: str, base_domain: str = "avito.ru") -> str:
    """
    Нормализует URL, добавляя протокол и базовый домен при необходимости.
    
    Args:
        url: URL для нормализации
        base_domain: Базовый домен
        
    Returns:
        Нормализованный URL
    """
    if not url:
        return ""
    
    # Если URL уже полный
    if url.startswith(("http://", "https://")):
        return url
    
    # Если это относительный путь
    if url.startswith("/"):
        return f"https://www.{base_domain}{url}"
    
    # Если это просто путь без слеша
    return f"https://www.{base_domain}/{url}"


def generate_item_id(item_data: Dict[str, Any]) -> str:
    """
    Генерирует уникальный ID для объявления на основе его данных.
    
    Args:
        item_data: Данные объявления
        
    Returns:
        Уникальный ID
    """
    # Используем заголовок и ссылку для генерации ID
    title = item_data.get("title", "")
    link = item_data.get("link", "")
    
    if not title and not link:
        return ""
    
    # Создаем строку для хэширования
    data_string = f"{title}|{link}"
    
    # Генерируем MD5 хэш
    return hashlib.md5(data_string.encode("utf-8")).hexdigest()


def validate_item_data(item_data: Dict[str, Any]) -> bool:
    """
    Проверяет валидность данных объявления.
    
    Args:
        item_data: Данные объявления
        
    Returns:
        True если данные валидны, иначе False
    """
    # Проверяем обязательные поля
    required_fields = ["title", "price", "link"]
    
    for field in required_fields:
        if not item_data.get(field):
            return False
    
    # Проверяем минимальную длину заголовка
    title = item_data.get("title", "")
    if len(title.strip()) < 3:
        return False
    
    # Проверяем, что цена положительная (или 0 для бесплатных)
    price = item_data.get("price", 0)
    if price < 0:
        return False
    
    return True


def format_price(price: int) -> str:
    """
    Форматирует цену для отображения.
    
    Args:
        price: Цена как целое число
        
    Returns:
        Отформатированная строка цены
    """
    if price == 0:
        return "Бесплатно"
    
    # Форматируем с разделителями тысяч
    return f"{price:,} ₽".replace(",", " ")


def extract_city_from_url(url: str) -> Optional[str]:
    """
    Извлекает название города из URL Авито.
    
    Args:
        url: URL Авито
        
    Returns:
        Название города или None
    """
    try:
        parsed = urlparse(url)
        path_parts = parsed.path.strip("/").split("/")
        
        if path_parts:
            # Первая часть пути обычно город
            city = path_parts[0]
            
            # Убираем возможные параметры
            city = city.split("?")[0]
            
            return city if city else None
        
    except Exception:
        pass
    
    return None


def build_avito_url(city: str, category: str, subcategory: str = None) -> str:
    """
    Строит URL для Авито на основе параметров.
    
    Args:
        city: Город (например, 'moskva')
        category: Категория (например, 'kvartiry')
        subcategory: Подкатегория (например, 'prodam')
        
    Returns:
        Сформированный URL
    """
    parts = [city, category]
    
    if subcategory:
        parts.append(subcategory)
    
    path = "/".join(parts)
    return f"https://www.avito.ru/{path}"