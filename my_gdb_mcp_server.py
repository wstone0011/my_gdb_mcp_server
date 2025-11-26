import sys
import pexpect
from fastmcp import FastMCP

# 定义 MCP 服务
mcp = FastMCP("GDB-MCP-Server")

class GDBManager:
    def __init__(self):
        self.child = None
        self.timeout_message = "[MCP Info] Execution timed out (likely running)."

    def start(self, command="gdb"):
        """启动 GDB 进程并配置环境"""
        if self.child and self.child.isalive():
            self.child.close()
        
        try:
            # 启动 GDB，使用 utf-8 编码
            self.child = pexpect.spawn(command, encoding='utf-8', timeout=5)
            
            # 1. 先等待启动完成，获取纯净的 Banner 信息
            # 这样可以避免初始化命令的回显混入启动信息中
            try:
                self.child.expect([r'\(gdb\)', r'pwndbg>', r'gef>', r'gdb-peda\$', r'\$'], timeout=5)
                startup_msg = self.child.before
            except pexpect.TIMEOUT:
                startup_msg = self.child.before if self.child.before else "GDB started (no prompt detected yet)"

            # 2. 发送初始化命令，并消耗掉回显
            # 逐条发送并等待，确保 Buffer 干净
            init_cmds = [
                "set pagination off",
                "set confirm off",
                "set width 0",
                "set height 0"
            ]
            
            for cmd in init_cmds:
                self.child.sendline(cmd)
                try:
                    # 快速等待提示符，消耗掉命令回显
                    self.child.expect([r'\(gdb\)', r'pwndbg>', r'gef>', r'gdb-peda\$', r'\$'], timeout=1)
                except pexpect.TIMEOUT:
                    pass 
            
            return f"GDB Started successfully.\nCommand: {command}\n\nInitial Output:\n{startup_msg}"
            
        except Exception as e:
            if self.child:
                self.child.close()
                self.child = None
            return f"Failed to start GDB: {str(e)}"

    def execute(self, cmd, timeout=10):
        """执行命令并获取输出"""
        if not self.child or not self.child.isalive():
            return "Error: GDB is not running. Please use start_debugging first."

        # 发送命令
        self.child.sendline(cmd)
        
        try:
            # 尝试匹配常见提示符
            # 注意：如果不改提示符，我们需要匹配各种可能的提示符
            # 这可能会导致在程序输出类似提示符的内容时误判，但在不做修改的前提下这是必须的
            index = self.child.expect([r'\(gdb\)', r'pwndbg>', r'gef>', r'gdb-peda\$', r'\$'], timeout=timeout)
            
            # 获取输出（包含 ANSI 码）
            output = self.child.before
            
            # 补上匹配到的提示符（为了完整性）
            # output += self.child.after # 通常不需要返回提示符，但为了原样输出可以考虑
            
            return output
            
        except pexpect.TIMEOUT:
            # 超时处理：通常意味着程序在运行中
            current_output = self.child.before if self.child.before else ""
            return f"{current_output}\n\n{self.timeout_message}"

        except Exception as e:
            return f"Error executing command: {str(e)}"

    def interrupt(self):
        """发送 SIGINT (Ctrl+C)"""
        if not self.child or not self.child.isalive():
            return "Error: GDB not running"
        
        self.child.sendintr()
        
        # 中断后，尝试读取输出
        try:
            self.child.expect([r'\(gdb\)', r'pwndbg>', r'gef>', r'gdb-peda\$', r'\$'], timeout=2)
            return f"Interrupted.\n{self.child.before}"
        except pexpect.TIMEOUT:
            return "Signal sent, but GDB prompt did not appear immediately."

    def stop(self):
        """停止调试会话"""
        if self.child:
            self.child.close()
            self.child = None
        return "GDB Session ended."

# 全局实例
gdb = GDBManager()

@mcp.tool()
def run_shell_command(command: str, timeout: int = 10):
    """
    在 MCP 服务器的宿主环境中执行系统 Shell 命令。
    
    该工具允许你直接运行宿主机的系统命令，如 `ps`, `ls`, `grep` 等，这对于辅助调试（如查找进程 ID、检查文件存在）非常有用。
    
    Args:
        command: 要执行的完整 Shell 命令字符串（例如 "ps aux | grep my_process"）。
        timeout: 命令执行的超时时间（秒），默认为 10 秒。
    
    Returns:
        包含 stdout, stderr 和退出码的完整输出。
    """
    try:
        import subprocess
        
        # 使用 subprocess.run 替代 os.popen 以获得更好的控制（包括超时）
        # capture_output=True 捕获 stdout 和 stderr
        # text=True 将输出解码为字符串
        result = subprocess.run(
            command, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=timeout
        )
        
        output = f"Command: {command}\n"
        output += f"Exit Code: {result.returncode}\n\n"
        
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"
        
        if result.stderr:
            output += f"STDERR:\n{result.stderr}\n"
            
        return output.strip()
        
    except subprocess.TimeoutExpired as e:
        output = f"Command: {command}\n"
        output += f"Error: Execution timed out after {timeout} seconds.\n"
        if e.stdout:
             output += f"\nPartial STDOUT:\n{e.stdout.decode('utf-8', errors='ignore')}\n"
        if e.stderr:
             output += f"\nPartial STDERR:\n{e.stderr.decode('utf-8', errors='ignore')}\n"
        return output
        
    except Exception as e:
        return f"Error executing shell command: {str(e)}"

@mcp.tool()
def start_debugging(command: str = "gdb"):
    """
    启动一个新的 GDB 调试会话。
    
    这是进行调试的第一步。该工具会启动 GDB 进程并建立通信管道。
    支持本地启动、附加进程和远程调试模式。
    
    Args:
        command: 启动 GDB 的完整 Shell 命令字符串。
            - 本地调试（推荐）: "gdb ./my_program" 
              此模式下，MCP 可以完整捕获被调试程序的标准输入输出（I/O）。
            - 自动执行脚本: "gdb -x debug_script.gdb ./my_program"
              支持通过 -x 参数在启动时加载并执行 GDB 脚本。
            - 附加到现有进程: "gdb -p <PID>"
            - 远程调试准备: "gdb" 
              启动空 GDB 后，请接着调用 `execute_command` 发送 `target remote <host>:<port>`。
    """
    return gdb.start(command)

@mcp.tool()
def send_gdb_command(command: str, timeout: int = 10):
    """
    向 GDB 会话发送命令或向被调试程序发送标准输入。
    
    这是与 GDB 进行双向交互的核心工具。
    
    功能：
    1. 执行 GDB 调试命令：如 `break main`, `step`, `print x`。
    2. 向程序发送输入：如果程序正在等待标准输入（stdin），通过此工具发送的内容会被传递给程序。
    
    Args:
        command: 要发送的命令字符串或输入数据。
        timeout: 等待输出的超时时间（秒）。
            - 对于耗时操作或程序持续运行（continue），超时是正常的，会返回当前已产生的输出。
    """
    return gdb.execute(command, timeout)

@mcp.tool()
def interrupt():
    """
    向 GDB 进程发送中断信号 (SIGINT/Ctrl+C)。
    
    使用场景：
    当程序正在运行（例如执行了 `continue` 后）且你想暂停它以检查状态时，调用此工具。
    这相当于在终端中按下了 Ctrl+C。
    """
    return gdb.interrupt()

@mcp.tool()
def stop_debugging():
    """
    终止当前的 GDB 调试会话。
    
    该工具会关闭 GDB 进程并清理资源。
    注意：如果只是想暂停程序运行，请使用 `interrupt` 工具，而不是这个。
    """
    return gdb.stop()

if __name__ == "__main__":
    mcp.run(transport="sse")
