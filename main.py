import time
import threading
from plc_modbus import PLCServer
from robot_controller import RobotController
from constants import RobotType, MODBUS_PORT
import process_steps

def main():
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
    process_steps.execute_full_process(robot_a, robot_b, plc_server)
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
