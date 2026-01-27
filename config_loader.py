"""
配置文件加载模块
支持从外部JSON配置文件加载机器人和系统配置

配置文件搜索顺序：
1. /config/robot_config.json (Docker挂载目录)
2. ./robot_config.json (当前目录)
3. 使用constants.py中的默认配置
"""

import os
import json
from typing import Dict, Any, Optional
from error_logger import get_error_logger

logger = get_error_logger()

# 配置文件搜索路径（按优先级排序）
CONFIG_SEARCH_PATHS = [
    "/config/robot_config.json",      # Docker挂载目录（优先）
    "./robot_config.json",            # 当前工作目录
    os.path.join(os.path.dirname(__file__), "robot_config.json"),  # 模块所在目录
]

# 全局配置缓存
_config_cache: Optional[Dict[str, Any]] = None
_config_path: Optional[str] = None


def find_config_file() -> Optional[str]:
    """
    查找配置文件
    
    返回:
        找到的配置文件路径，如果没找到返回None
    """
    for path in CONFIG_SEARCH_PATHS:
        if os.path.exists(path):
            return path
    return None


def load_config(force_reload: bool = False) -> Dict[str, Any]:
    """
    加载配置文件
    
    参数:
        force_reload: 是否强制重新加载
    
    返回:
        配置字典
    """
    global _config_cache, _config_path
    
    if _config_cache is not None and not force_reload:
        return _config_cache
    
    config_path = find_config_file()
    
    if config_path:
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                _config_cache = json.load(f)
            _config_path = config_path
            logger.info("配置加载", f"✓ 已加载外部配置文件: {config_path}")
            print(f"✓ 已加载外部配置文件: {config_path}")
            return _config_cache
        except json.JSONDecodeError as e:
            logger.error("配置加载", f"配置文件格式错误: {config_path}, {e}")
            print(f"⚠️  配置文件格式错误: {config_path}")
        except Exception as e:
            logger.error("配置加载", f"读取配置文件失败: {config_path}, {e}")
            print(f"⚠️  读取配置文件失败: {config_path}")
    
    # 没找到或加载失败，返回空配置
    logger.info("配置加载", "未找到外部配置文件，使用默认配置")
    print("ℹ️  未找到外部配置文件，使用constants.py中的默认配置")
    _config_cache = {}
    _config_path = None
    return _config_cache


def get_config_path() -> Optional[str]:
    """获取当前使用的配置文件路径"""
    return _config_path


def get_robot_configs() -> Dict[str, Dict[str, Any]]:
    """
    获取机器人配置
    
    返回:
        机器人配置字典，格式:
        {
            "robot_id": {
                "host": "ip_address",
                "port": "port",
                "robot_type": "type_string",
                "enabled": true/false
            }
        }
    """
    config = load_config()
    robots_config = config.get("robots", {})
    
    # 过滤掉禁用的机器人
    enabled_robots = {}
    for robot_id, robot_config in robots_config.items():
        if robot_config.get("enabled", True):
            enabled_robots[robot_id] = robot_config
    
    return enabled_robots


def get_http_server_port() -> int:
    """获取HTTP服务器端口"""
    config = load_config()
    return config.get("http_server", {}).get("port", 8090)


def get_auto_charging_config() -> Dict[str, Any]:
    """
    获取自动充电配置
    
    返回:
        {
            "enabled": bool,
            "check_interval": int,
            "low_threshold": float,
            "full_threshold": float
        }
    """
    config = load_config()
    return config.get("auto_charging", {})


def display_config_info():
    """显示当前配置信息"""
    config = load_config()
    config_path = get_config_path()
    
    print("\n" + "="*60)
    print("📋 配置信息")
    print("="*60)
    
    if config_path:
        print(f"配置文件: {config_path}")
    else:
        print("配置文件: 使用默认配置 (constants.py)")
    
    # 显示机器人配置
    robots = config.get("robots", {})
    if robots:
        print(f"\n机器人配置 ({len(robots)} 个):")
        for robot_id, robot_config in robots.items():
            enabled = "✓" if robot_config.get("enabled", True) else "✗"
            host = robot_config.get("host", "未配置")
            port = robot_config.get("port", "未配置")
            print(f"  [{enabled}] {robot_id}: {host}:{port}")
    else:
        print("\n机器人配置: 使用默认配置")
    
    # 显示HTTP服务器配置
    http_config = config.get("http_server", {})
    if http_config:
        print(f"\nHTTP服务器端口: {http_config.get('port', 8090)}")
    
    # 显示自动充电配置
    charging_config = config.get("auto_charging", {})
    if charging_config:
        enabled = "开启" if charging_config.get("enabled", True) else "关闭"
        print(f"\n自动充电功能: {enabled}")
        if charging_config.get("enabled", True):
            require_on_startup = charging_config.get("require_battery_on_startup", True)
            print(f"  启动时等待电量: {'是' if require_on_startup else '否'}")
            if require_on_startup:
                wait_timeout = charging_config.get("battery_wait_timeout", 60)
                print(f"  等待超时: {wait_timeout}秒" if wait_timeout > 0 else "  等待超时: 无限等待")
            print(f"  检测间隔: {charging_config.get('check_interval', 600)}秒")
            print(f"  低电量阈值: {charging_config.get('low_threshold', 0.30)*100:.0f}%")
            print(f"  充满阈值: {charging_config.get('full_threshold', 0.80)*100:.0f}%")
    
    print("="*60 + "\n")


# 导出便捷函数
__all__ = [
    'load_config',
    'get_config_path',
    'get_robot_configs',
    'get_http_server_port',
    'get_auto_charging_config',
    'display_config_info',
]

