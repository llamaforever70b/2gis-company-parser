"""
Менеджер для сохранения и восстановления прогресса парсинга.
Позволяет продолжить работу с места остановки после сбоя или перезапуска.
"""

import json
import datetime
from pathlib import Path
from typing import Set, Optional, List, Dict, Any
import logging


class ResumeManager:
    """
    Управляет сохранением прогресса парсинга.
    Позволяет продолжить с места остановки после сбоя/перезапуска.
    
    Attributes:
        progress_file: Путь к файлу с прогрессом
        processed_urls: Множество обработанных URL
        total_collected: Общее количество собранных компаний
        logger: Логгер для отслеживания операций
    """
    
    def __init__(self, progress_file: str = "output_2gis/progress.json", logger: Optional[logging.Logger] = None):
        """
        Инициализация менеджера прогресса.
        
        Args:
            progress_file: Путь к файлу для сохранения прогресса
            logger: Логгер для записи операций (опционально)
        """
        self.progress_file = Path(progress_file)
        self.processed_urls: Set[str] = set()
        self.total_collected: int = 0
        self.logger = logger or logging.getLogger(__name__)
        self._load()
    
    def _normalize_url(self, url: str) -> str:
        """
        Нормализует URL для сравнения.
        Убирает query-параметры, которые могут меняться между запусками.
        
        Args:
            url: Исходный URL
            
        Returns:
            Нормализованный URL без query-параметров
        """
        # Убираем ?stat= и другие параметры, которые меняются
        base_url = url.split('?')[0] if '?' in url else url
        return base_url.strip()
    
    def _load(self) -> None:
        """
        Загружает сохранённый прогресс из файла.
        При ошибках чтения создаёт новый пустой прогресс.
        """
        if not self.progress_file.exists():
            self.logger.info("Файл прогресса не найден, начинаем с чистого листа")
            return
        
        try:
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.processed_urls = set(data.get('processed_urls', []))
                self.total_collected = data.get('total_collected', 0)
                last_updated = data.get('last_updated', 'неизвестно')
                
                self.logger.info(f"✓ Загружен прогресс: {self.total_collected} компаний")
                self.logger.info(f"  Последнее обновление: {last_updated}")
                self.logger.info(f"  Обработано URL: {len(self.processed_urls)}")
                
        except json.JSONDecodeError as e:
            self.logger.error(f"Ошибка чтения JSON из {self.progress_file}: {e}")
            self.logger.warning("Начинаем с чистого листа")
            self.processed_urls = set()
            self.total_collected = 0
            
        except Exception as e:
            self.logger.error(f"Неожиданная ошибка при загрузке прогресса: {e}")
            self.logger.warning("Начинаем с чистого листа")
            self.processed_urls = set()
            self.total_collected = 0
    
    def save(self) -> bool:
        """
        Сохраняет текущий прогресс в файл.
        
        Returns:
            True если сохранение успешно, False при ошибке
        """
        try:
            # Создаём директорию если её нет
            self.progress_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Формируем данные для сохранения
            data = {
                'processed_urls': list(self.processed_urls),
                'total_collected': self.total_collected,
                'last_updated': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Сохраняем в файл
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            self.logger.debug(f"Прогресс сохранён: {self.total_collected} компаний")
            return True
            
        except PermissionError:
            self.logger.error(f"Нет доступа для записи в {self.progress_file}")
            return False
            
        except Exception as e:
            self.logger.error(f"Ошибка при сохранении прогресса: {e}")
            return False
    
    def is_processed(self, url: str) -> bool:
        """
        Проверяет, была ли уже обработана компания по URL.
        
        Args:
            url: URL компании для проверки
            
        Returns:
            True если компания уже обработана, False если нет
        """
        normalized = self._normalize_url(url)
        return normalized in self.processed_urls
    
    def mark_processed(self, url: str) -> None:
        """
        Отмечает URL как обработанный.
        
        Args:
            url: URL обработанной компании
        """
        normalized = self._normalize_url(url)
        if normalized not in self.processed_urls:
            self.processed_urls.add(normalized)
            self.total_collected += 1
            self.logger.debug(f"Отмечен как обработанный: {normalized}")
    
    def get_remaining_urls(self, all_urls: List[str]) -> List[str]:
        """
        Возвращает список URL, которые ещё не обработаны.
        
        Args:
            all_urls: Полный список URL для обработки
            
        Returns:
            Список необработанных URL
        """
        remaining = [url for url in all_urls if not self.is_processed(url)]
        
        skipped_count = len(all_urls) - len(remaining)
        if skipped_count > 0:
            self.logger.info(f"Пропущено из предыдущих сессий: {skipped_count} компаний")
        
        return remaining
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Возвращает статистику по прогрессу.
        
        Returns:
            Словарь со статистикой
        """
        return {
            'total_collected': self.total_collected,
            'processed_urls_count': len(self.processed_urls),
            'progress_file': str(self.progress_file),
            'file_exists': self.progress_file.exists()
        }
    
    def clear(self) -> bool:
        """
        Очищает сохранённый прогресс (удаляет файл).
        
        Returns:
            True если очистка успешна, False при ошибке
        """
        try:
            if self.progress_file.exists():
                self.progress_file.unlink()
                self.logger.info(f"Файл прогресса удалён: {self.progress_file}")
            
            self.processed_urls = set()
            self.total_collected = 0
            return True
            
        except Exception as e:
            self.logger.error(f"Ошибка при удалении файла прогресса: {e}")
            return False
