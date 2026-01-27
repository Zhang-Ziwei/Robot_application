#!/usr/bin/env python3
"""
模拟模式测试脚本
在不连接实际机器人的情况下测试HTTP命令和信息传递功能
支持交互式任务队列测试
"""

import sys
import time
import threading
import json
import requests
from mock_robot_controller import MockRobotController
from constants import RobotType, MODBUS_PORT, HTTP_SERVER_PORT
from cmd_handler import init_cmd_handler, get_cmd_handler
from http_server import get_http_server
from task_queue import get_task_queue
from error_logger import get_error_logger

logger = get_error_logger()

SERVER_URL = f"http://localhost:{HTTP_SERVER_PORT}"


def interactive_test(task_queue, robot_a, robot_b, http_server):
    """交互式测试模式"""
    print("\n" + "="*70)
    print("🎮 交互式测试模式")
    print("="*70)
    
    submitted_tasks = []
    
    while True:
        print("\n" + "-"*70)
        print("请选择操作:")
        print("  1. 发送BOTTLE_GET命令（查询瓶子）")
        print("  2. 发送PICK_UP命令（拾取瓶子）")
        print("  3. 发送PUT_TO命令（放置瓶子）")
        print("  4. 快速发送3个测试命令")
        print("  5. 查询队列状态")
        print("  6. 查询所有任务状态")
        print("  7. 监控任务执行（实时）")
        print("  0. 退出测试")
        print("-"*70)
        
        choice = input("\n输入选项 [0-7]: ").strip()
        
        if choice == "1":
            task_id = send_command("test_commands/bottle_get_command.json")
            if task_id:
                submitted_tasks.append(task_id)
        
        elif choice == "2":
            task_id = send_command("test_commands/pickup_command.json")
            if task_id:
                submitted_tasks.append(task_id)
        
        elif choice == "3":
            task_id = send_command("test_commands/put_to_command.json")
            if task_id:
                submitted_tasks.append(task_id)
        
        elif choice == "4":
            print("\n快速发送3个测试命令...")
            commands = [
                "test_commands/bottle_get_command.json",
                "test_commands/pickup_command.json",
                "test_commands/put_to_command.json"
            ]
            for cmd_file in commands:
                task_id = send_command(cmd_file)
                if task_id:
                    submitted_tasks.append(task_id)
                time.sleep(0.5)
        
        elif choice == "5":
            show_queue_status()
        
        elif choice == "6":
            show_all_tasks(submitted_tasks)
        
        elif choice == "7":
            monitor_tasks(submitted_tasks)
        
        elif choice == "0":
            print("\n退出交互式测试...")
            break
        
        else:
            print("\n⚠️  无效选项，请重新选择")


def send_command(cmd_file):
    """发送命令到服务器"""
    try:
        with open(cmd_file, 'r') as f:
            cmd_data = json.load(f)
        
        cmd_name = cmd_data.get('cmd_type', 'UNKNOWN')
        print(f"\n📤 发送命令: {cmd_name}")
        
        response = requests.post(SERVER_URL, json=cmd_data, timeout=5)
        result = response.json()
        
        if result.get('success'):
            task_id = result.get('task_id')
            queue_size = result.get('queue_size', 0)
            print(f"  ✓ 任务已提交")
            print(f"    任务ID: {task_id}")
            print(f"    队列位置: {queue_size}")
            return task_id
        else:
            print(f"  ✗ 提交失败: {result.get('message')}")
            return None
    
    except FileNotFoundError:
        print(f"  ✗ 找不到文件: {cmd_file}")
        return None
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        return None


def show_queue_status():
    """显示队列状态"""
    try:
        response = requests.get(f"{SERVER_URL}/queue/status", timeout=2)
        status = response.json()
        
        print("\n📊 队列状态:")
        print(f"  运行中: {status.get('running')}")
        print(f"  队列长度: {status.get('queue_size')}")
        print(f"  当前任务: {status.get('current_task', '无')}")
        print(f"  总任务数: {status.get('total_tasks')}")
        
        status_count = status.get('status_count', {})
        print(f"\n任务统计:")
        print(f"  ⏳ 等待中: {status_count.get('pending', 0)}")
        print(f"  ▶️  执行中: {status_count.get('running', 0)}")
        print(f"  ✅ 已完成: {status_count.get('completed', 0)}")
        print(f"  ❌ 失败: {status_count.get('failed', 0)}")
    
    except Exception as e:
        print(f"\n✗ 查询失败: {e}")


def show_all_tasks(task_ids):
    """显示所有任务状态"""
    if not task_ids:
        print("\n⚠️  还没有提交任何任务")
        return
    
    print(f"\n📋 所有任务状态（共 {len(task_ids)} 个）:")
    print("-"*70)
    
    for task_id in task_ids:
        try:
            response = requests.get(f"{SERVER_URL}/task/{task_id}", timeout=2)
            result = response.json()
            
            if result.get('success'):
                task = result.get('task', {})
                status = task.get('status')
                cmd_type = task.get('cmd_type')
                
                # 状态图标
                if status == 'pending':
                    icon = "⏳"
                    status_text = "等待中"
                elif status == 'running':
                    icon = "▶️"
                    status_text = "执行中"
                elif status == 'completed':
                    icon = "✅"
                    duration = task.get('duration', 0)
                    status_text = f"完成 ({duration:.1f}秒)"
                elif status == 'failed':
                    icon = "❌"
                    status_text = "失败"
                else:
                    icon = "❓"
                    status_text = status
                
                print(f"{icon} {task_id} ({cmd_type}): {status_text}")
            else:
                print(f"✗ {task_id}: 查询失败")
        
        except Exception as e:
            print(f"✗ {task_id}: 错误 - {e}")
    
    print("-"*70)


def monitor_tasks(task_ids):
    """实时监控任务执行"""
    if not task_ids:
        print("\n⚠️  还没有提交任何任务")
        return
    
    print(f"\n🔍 实时监控模式（每2秒刷新）")
    print("按 Ctrl+C 返回主菜单\n")
    
    try:
        while True:
            all_done = True
            
            for task_id in task_ids:
                try:
                    response = requests.get(f"{SERVER_URL}/task/{task_id}", timeout=2)
                    result = response.json()
                    
                    if result.get('success'):
                        task = result.get('task', {})
                        status = task.get('status')
                        cmd_type = task.get('cmd_type')
                        
                        if status == 'pending':
                            print(f"⏳ {task_id} ({cmd_type}): 等待中...")
                            all_done = False
                        elif status == 'running':
                            print(f"▶️  {task_id} ({cmd_type}): 执行中...")
                            all_done = False
                        elif status == 'completed':
                            duration = task.get('duration', 0)
                            print(f"✅ {task_id} ({cmd_type}): 完成 (耗时: {duration:.1f}秒)")
                        elif status == 'failed':
                            error = task.get('error', '未知错误')
                            print(f"❌ {task_id} ({cmd_type}): 失败 - {error}")
                
                except Exception:
                    pass
            
            if all_done:
                print("\n✅ 所有任务执行完成！")
                input("\n按 Enter 返回主菜单...")
                break
            
            print("-" * 70)
            time.sleep(2)
    
    except KeyboardInterrupt:
        print("\n\n监控已停止，返回主菜单")


def main():
    print("\n" + "="*70)
    print("🧪 模拟模式测试 - HTTP命令信息传递测试")
    print("="*70)
    print("\n本测试将:")
    print("  1. 使用模拟机器人控制器（无需实际连接）")
    print("  2. 启动HTTP服务器接收命令")
    print("  3. 显示所有会发送给机器人的命令信息")
    print("  4. 生成完整的测试报告")
    print("\n" + "="*70 + "\n")
    
    # 初始化模拟机器人
    print("步骤1: 初始化模拟机器人控制器")
    print("-" * 70)
    
    robot_a = MockRobotController(
        "192.168.217.100",
        "9091",
        RobotType.ROBOT_A
    )
    
    robot_b = MockRobotController(
        "192.168.217.80",
        "9090",
        RobotType.ROBOT_B
    )
    
    # 模拟连接
    print("\n步骤2: 模拟机器人连接")
    print("-" * 70)
    robot_a.connect()
    robot_b.connect()
    
    # 初始化命令处理器
    print("\n步骤3: 初始化命令处理器")
    print("-" * 70)
    init_cmd_handler(robot_a, robot_b)
    print("✓ 命令处理器初始化完成\n")
    
    # 启动任务队列
    print("\n步骤4: 启动任务队列")
    print("-" * 70)
    task_queue = get_task_queue()
    task_queue.start()
    print("✓ 任务队列已启动\n")
    
    # 启动HTTP服务器
    print("\n步骤5: 启动HTTP服务器（队列模式）")
    print("-" * 70)
    http_server = get_http_server(host='0.0.0.0', port=HTTP_SERVER_PORT)
    http_server.set_command_callback(lambda cmd: get_cmd_handler().handle_command(cmd))
    http_server.set_task_queue(task_queue)
    http_server.start()
    
    print("\n" + "="*70)
    print("✅ 模拟测试环境准备完成（任务队列模式）")
    print("="*70)
    print(f"\nHTTP服务器已启动: http://localhost:{HTTP_SERVER_PORT}")
    
    logger.info("模拟测试", "模拟测试环境启动成功")
    
    # 询问运行模式
    print("\n选择测试模式:")
    print("  1. 交互式测试（推荐）- 菜单操作，方便测试")
    print("  2. 等待模式 - 等待外部curl命令")
    
    mode = input("\n请选择模式 (1/2) [默认: 1]: ").strip() or "1"
    
    try:
        if mode == "1":
            # 交互式测试模式
            interactive_test(task_queue, robot_a, robot_b, http_server)
        else:
            # 等待模式
            print("\n✨ 任务队列特性:")
            print("  - 多个命令会自动排队")
            print("  - 一个命令执行完成后再执行下一个")
            print("  - 所有机器人命令都会在此终端显示")
            print("\n可以在新终端使用curl发送命令:")
            print(f"  curl -X POST http://localhost:{HTTP_SERVER_PORT} -d @test_commands/bottle_get_command.json")
            print("\n按 Ctrl+C 停止测试并查看统计信息\n")
            
            # 保持运行
            while True:
                time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n\n" + "="*70)
        print("🛑 停止测试")
        print("="*70)
        
        # 显示统计信息
        print("\n生成测试报告...")
        
        # Robot A 统计
        robot_a.print_request_summary()
        
        # Robot B 统计
        robot_b.print_request_summary()
        
        # 保存日志
        print("\n保存请求日志...")
        robot_a.save_requests_to_file("mock_robot_a_requests.json")
        robot_b.save_requests_to_file("mock_robot_b_requests.json")
        
        # 停止服务器
        print("\n停止HTTP服务器...")
        http_server.stop()
        
        # 停止任务队列
        print("\n停止任务队列...")
        task_queue.stop()
        
        # 关闭模拟机器人
        robot_a.close()
        robot_b.close()
        
        print("\n" + "="*70)
        print("✅ 测试完成")
        print("="*70)
        print("\n查看详细日志:")
        print("  - 请求日志: mock_robot_a_requests.json")
        print("  - 请求日志: mock_robot_b_requests.json")
        print("  - 系统日志: logs/error_log_*.txt")
        print("\n" + "="*70 + "\n")
        
        logger.info("模拟测试", "模拟测试完成")


if __name__ == "__main__":
    main()

