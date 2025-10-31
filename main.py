import time
import threading
import sys
from plc_modbus import PLCServer
from robot_controller import RobotController
from constants import RobotType, MODBUS_PORT
import process_steps
from error_logger import get_error_logger
from file_lock import ensure_single_instance

def main():
    # 检查程序是否已在运行（文件锁）
    lock = ensure_single_instance("robot_control.lock")
    if not lock:
        sys.exit(1)
    
    # 初始化错误日志
    logger = get_error_logger()
    logger.info("系统", "机器人控制系统启动")
    print(f"日志文件: {logger.get_log_file()}")
    
    # 初始化组件
    plc_server = PLCServer()
    
    # 初始化机器人控制器，配置自动重连参数：
    # max_retry_attempts: None=无限重试, 数字=最大重试次数
    # retry_interval: 重试间隔（秒）
    robot_a = RobotController(
        "192.168.217.100", 
        "9091", 
        RobotType.ROBOT_A,
        max_retry_attempts=None,  # 无限重试直到连接成功
        retry_interval=5  # 每5秒重试一次
    )
    
    robot_b = RobotController(
        "192.168.217.80", 
        "9090", 
        RobotType.ROBOT_B,
        max_retry_attempts=None,  # 无限重试直到连接成功
        retry_interval=5  # 每5秒重试一次
    )
    
    # 启动PLC服务器
    plc_server.start_server(port=MODBUS_PORT)
    # 等待所有连接就绪
    print("Waiting for all connections to be ready...")
    time.sleep(2)  # 简单等待，实际应用中可能需要更复杂的检查
    # 连接机器人
    #print("Connecting to robots...")
    robot_a_connected = robot_a.connect()
    robot_b_connected = robot_b.connect()
    # 启动自动复位线圈线程
    # process_steps.execute_plc_process(plc_server)
    #process_steps.execute_robotA_test(robot_a, plc_server)      # 单独运行机器人A
    #process_steps.execute_test_process(robot_b, plc_server)    # 单独运行机器人B
    
    try:
        cycle_count = 0
        while input('是否进入下个循环，输入y/n') == 'y':
            cycle_count += 1
            logger.info("系统", f"开始执行第 {cycle_count} 次完整流程")
            try:
                process_steps.execute_full_process(robot_a, robot_b, plc_server)
                logger.info("系统", f"第 {cycle_count} 次完整流程执行完成")
            except Exception as e:
                logger.exception_occurred("系统", f"第{cycle_count}次流程执行", e)
                print(f"执行出错: {e}")
    except KeyboardInterrupt:
        logger.warning("系统", "用户中断程序 (Ctrl+C)")
        print("\n程序被用户中断")
    except Exception as e:
        logger.exception_occurred("系统", "主循环", e)
        print(f"发生错误: {e}")
    finally:
        logger.info("系统", "开始清理资源")
        robot_a.close()
        robot_b.close()
        plc_server.stop()
        logger.info("系统", "机器人控制系统已停止")
        # 释放文件锁
        if lock:
            lock.release()
            print("程序锁已释放")
    #if(process_steps.execute_full_process(robot_a, robot_b, plc_server) and input('是否进入下个循环，输入y/n') == 'n'): # 全流程测试
        #process_steps.execute_test_process(robot_b, plc_server)

    '''   
    if not robot_a_connected or not robot_b_connected:
        print("Failed to connect to one or more robots")
        plc_server.stop()
        return
    
    # 等待所有连接就绪
    print("Waiting for all connections to be ready...")
    time.sleep(2)  # 简单等待，实际应用中可能需要更复杂的检查
    
    try:
        # 执行完整流程，类型为1
        success = process_steps.execute_full_process(robot_a, robot_b, plc_server, 1)
        
        if success:
            print("Full process executed successfully")
        else:
            print("Full process failed")
            
        # 可以根据需要执行其他测试流程
        # process_steps.execute_robotA_test(robot_a, plc_server)
        
    except KeyboardInterrupt:
        print("Process interrupted by user")
    finally:
        # 清理资源
        robot_a.close()
        robot_b.close()
        plc_server.stop()
        print("All resources cleaned up")
'''
if __name__ == "__main__":
    main()
