"""
存储管理器模块
负责后部暂存区状态的持久化管理
"""

import json
import os
from typing import Dict, List
from constants import DEFAULT_BACK_TEMP_STORAGE, STORAGE_STATE_FILE
from error_logger import get_error_logger

logger = get_error_logger()

class StorageManager:
    """存储状态管理器"""
    
    def __init__(self, state_file: str = STORAGE_STATE_FILE):
        self.state_file = state_file
        self._storage = None
    
    def load_storage(self) -> Dict[str, List]:
        """从文件加载存储状态"""
        if not os.path.exists(self.state_file):
            logger.info("存储管理器", f"状态文件不存在，使用默认配置")
            self._storage = self._get_default_storage()
            return self._storage
        
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                self._storage = json.load(f)
            logger.info("存储管理器", f"成功加载存储状态: {self.state_file}")
            return self._storage
        except Exception as e:
            logger.error("存储管理器", f"加载存储状态失败: {e}, 使用默认配置")
            self._storage = self._get_default_storage()
            return self._storage
    
    def save_storage(self, storage: Dict[str, List] = None) -> bool:
        """保存存储状态到文件"""
        if storage is not None:
            self._storage = storage
        
        if self._storage is None:
            logger.error("存储管理器", "没有存储状态可保存")
            return False
        
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self._storage, f, indent=4, ensure_ascii=False)
            logger.info("存储管理器", f"存储状态已保存到: {self.state_file}")
            return True
        except Exception as e:
            logger.error("存储管理器", f"保存存储状态失败: {e}")
            return False
    
    def reset_storage(self) -> Dict[str, List]:
        """重置存储状态为默认值"""
        self._storage = self._get_default_storage()
        logger.info("存储管理器", "存储状态已重置为默认值")
        return self._storage
    
    def get_storage(self) -> Dict[str, List]:
        """获取当前存储状态"""
        if self._storage is None:
            return self.load_storage()
        return self._storage
    
    def _get_default_storage(self) -> Dict[str, List]:
        """获取默认存储配置的深拷贝"""
        import copy
        return copy.deepcopy(DEFAULT_BACK_TEMP_STORAGE)
    
    def display_storage_status(self) -> str:
        """显示存储状态的格式化字符串"""
        if self._storage is None:
            self.load_storage()
        
        status_lines = ["当前暂存区状态:"]
        status_lines.append("=" * 60)
        
        for bottle_type, slots in self._storage.items():
            occupied = sum(1 for slot in slots if slot != 0)
            total = len(slots)
            status_lines.append(f"{bottle_type}:")
            status_lines.append(f"  占用: {occupied}/{total}")
            status_lines.append(f"  状态: {slots}")
        
        status_lines.append("=" * 60)
        return "\n".join(status_lines)
    
    def update_slot(self, bottle_type: str, slot_index: int, bottle_id: str) -> bool:
        """更新指定槽位"""
        if self._storage is None:
            self.load_storage()
        
        if bottle_type not in self._storage:
            logger.error("存储管理器", f"无效的瓶子类型: {bottle_type}")
            return False
        
        if slot_index < 0 or slot_index >= len(self._storage[bottle_type]):
            logger.error("存储管理器", f"无效的槽位索引: {slot_index}")
            return False
        
        self._storage[bottle_type][slot_index] = bottle_id
        self.save_storage()
        return True
    
    def clear_slot(self, bottle_type: str, slot_index: int) -> bool:
        """清空指定槽位"""
        return self.update_slot(bottle_type, slot_index, 0)
    
    def get_empty_slot_index(self, bottle_type: str) -> int:
        """获取指定类型的第一个空槽位索引"""
        if self._storage is None:
            self.load_storage()
        
        if bottle_type not in self._storage:
            return None
        
        for i, slot in enumerate(self._storage[bottle_type]):
            if slot == 0:
                return i
        return None
    
    def is_full(self) -> bool:
        """检查所有暂存区是否已满"""
        if self._storage is None:
            self.load_storage()
        
        for bottle_type, slots in self._storage.items():
            if 0 in slots:
                return False
        return True
    
    def is_type_full(self, bottle_type: str) -> bool:
        """检查指定类型的暂存区是否已满"""
        if self._storage is None:
            self.load_storage()
        
        if bottle_type not in self._storage:
            return True
        
        return 0 not in self._storage[bottle_type]


# 全局存储管理器实例
_storage_manager = None

def init_storage_manager(reset: bool = False) -> StorageManager:
    """
    初始化存储管理器
    
    Args:
        reset: 是否重置为默认值
    
    Returns:
        StorageManager实例
    """
    global _storage_manager
    _storage_manager = StorageManager()
    
    if reset:
        _storage_manager.reset_storage()
        _storage_manager.save_storage()
        logger.info("存储管理器", "存储状态已重置并保存")
    else:
        _storage_manager.load_storage()
        logger.info("存储管理器", "存储状态已加载")
    
    return _storage_manager

def get_storage_manager() -> StorageManager:
    """获取存储管理器实例"""
    global _storage_manager
    if _storage_manager is None:
        raise RuntimeError("存储管理器未初始化，请先调用init_storage_manager")
    return _storage_manager

def get_back_temp_storage() -> Dict[str, List]:
    """获取后部暂存区状态（便捷函数）"""
    return get_storage_manager().get_storage()

def save_back_temp_storage(storage: Dict[str, List] = None):
    """保存后部暂存区状态（便捷函数）"""
    get_storage_manager().save_storage(storage)

